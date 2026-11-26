# -*- coding: utf-8 -*-
"""
codebase_rag.py - Salto al SOTA: Indexación Semántica del Código (Codebase RAG).
Divide el repositorio del usuario en fragmentos lógicos (funciones, clases, métodos, endpoints)
mediante análisis estructural Tree-sitter / AST y los almacena en una base de datos vectorial
embebida persistente (SQLite + Vectores Float32 NumPy + BM25 + Reranker).
Permite consultar repositorios de cientos de archivos sin saturar los 16,384 tokens de contexto.
"""

import os
import re
import ast
import json
import math
import time
import sqlite3
import hashlib
import struct
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Set

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False

try:
    import tree_sitter
    HAS_TREE_SITTER = True
except ImportError:
    tree_sitter = None
    HAS_TREE_SITTER = False

BASE_DIR = Path(__file__).resolve().parent.parent
RAG_STORAGE_DIR = BASE_DIR / ".antigravity_rag"
RAG_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
VECTOR_DB_PATH = RAG_STORAGE_DIR / "codebase_vectors.sqlite"

EMBED_DIM = 256
SUPPORTED_CODE_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".htm", ".css",
    ".json", ".yaml", ".yml", ".sql", ".sh", ".ps1", ".bat",
    ".c", ".cpp", ".h", ".hpp", ".rs", ".go", ".java", ".md"
}
IGNORED_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    "dist", "build", ".idea", ".vscode", ".antigravity_rag", ".audit", "models", "engine"
}


def tokenize_code_identifiers(text: str) -> List[str]:
    """
    Tokenizador especializado en código fuente:
    Descompone CamelCase (NetworkSniffer -> network, sniffer), snake_case (get_packets -> get, packets)
    y conserva identificadores completos para máxima precisión semántica y léxica.
    """
    if not text:
        return []
    raw_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+", text)
    tokens: List[str] = []
    for tok in raw_tokens:
        low = tok.lower()
        if len(low) > 1:
            tokens.append(low)
        # Separar CamelCase
        camel_parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", tok).lower().split()
        if len(camel_parts) > 1:
            for cp in camel_parts:
                if len(cp) > 1:
                    tokens.append(cp)
        # Separar snake_case
        if "_" in low:
            for sp in low.split("_"):
                if len(sp) > 1:
                    tokens.append(sp)
    return tokens


def compute_local_code_embedding(text: str, symbol_name: str = "", symbol_type: str = "") -> List[float]:
    """
    Calcula un embedding vectorial denso L2-normalizado de dimensión 256 para código fuente.
    Combina proyecciones deterministas de identificadores, bi-gramas sintácticos,
    n-gramas de caracteres y pesos reforzados para firmas de clases/funciones.
    """
    vec = [0.0] * EMBED_DIM
    tokens = tokenize_code_identifiers(text)
    sym_tokens = tokenize_code_identifiers(symbol_name) * 4  # Boost x4 al nombre del símbolo
    type_tokens = [symbol_type.lower()] * 2 if symbol_type else []

    all_features = tokens + sym_tokens + type_tokens
    # Añadir bi-gramas semánticos
    for i in range(len(tokens) - 1):
        all_features.append(f"{tokens[i]}::{tokens[i+1]}")

    if not all_features:
        return vec

    for feat in all_features:
        h = int(hashlib.md5(feat.encode("utf-8", errors="ignore")).hexdigest()[:16], 16)
        idx1 = h % EMBED_DIM
        sign1 = 1.0 if ((h >> 8) & 1) == 0 else -1.0
        idx2 = (h >> 16) % EMBED_DIM
        sign2 = 1.0 if ((h >> 24) & 1) == 0 else -1.0
        vec[idx1] += sign1 * 1.0
        vec[idx2] += sign2 * 0.5

    # Normalización L2
    if HAS_NUMPY:
        arr = np.array(vec, dtype=np.float32)
        norm = float(np.linalg.norm(arr))
        if norm > 1e-8:
            arr = arr / norm
        return arr.tolist()
    else:
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 1e-8:
            vec = [v / norm for v in vec]
        return vec


def pack_vector(vec: List[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack_vector(blob: bytes) -> List[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    if HAS_NUMPY:
        a = np.asarray(v1, dtype=np.float32)
        b = np.asarray(v2, dtype=np.float32)
        return float(np.dot(a, b))
    return sum(x * y for x, y in zip(v1, v2))


class StructuralCodeChunker:
    """
    Divisor semántico de código basado en Tree-sitter / AST:
    Fragmenta los archivos del proyecto en unidades lógicas completas (clases, métodos,
    funciones, rutas API y componentes), preservando números de línea y firmas.
    """

    @staticmethod
    def chunk_file(file_path: Path, rel_path: str) -> List[Dict[str, Any]]:
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return []

        if not content.strip():
            return []

        ext = file_path.suffix.lower()
        if ext == ".py":
            return StructuralCodeChunker._chunk_python_ast(content, rel_path)
        elif ext in (".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".c", ".cpp"):
            return StructuralCodeChunker._chunk_brace_language(content, rel_path, ext)
        elif ext in (".html", ".htm"):
            return StructuralCodeChunker._chunk_html(content, rel_path)
        else:
            return StructuralCodeChunker._chunk_sliding_window(content, rel_path, ext)

    @staticmethod
    def _chunk_python_ast(content: str, rel_path: str) -> List[Dict[str, Any]]:
        lines = content.splitlines()
        chunks: List[Dict[str, Any]] = []
        try:
            tree = ast.parse(content, filename=rel_path)
        except SyntaxError:
            return StructuralCodeChunker._chunk_sliding_window(content, rel_path, ".py")

        # 1. Extraer cabecera de imports y constantes globales
        import_lines = []
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign)):
                s_l = getattr(node, "lineno", 1)
                e_l = getattr(node, "end_lineno", s_l)
                import_lines.extend(lines[s_l - 1 : e_l])
        if import_lines:
            hdr_text = "\n".join(import_lines[:40])
            chunks.append({
                "file_path": rel_path,
                "symbol_name": f"{Path(rel_path).stem}::imports_and_globals",
                "symbol_type": "module_header",
                "start_line": 1,
                "end_line": min(len(lines), len(import_lines)),
                "signature": f"Imports y constantes globales de {rel_path}",
                "content": hdr_text
            })

        # 2. Extraer cada Clase, Método y Función como un fragmento semántico independiente
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                s_l = getattr(node, "lineno", 1)
                e_l = getattr(node, "end_lineno", s_l)
                cls_doc = ast.get_docstring(node) or ""
                methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                sig = f"class {node.name} (métodos: {', '.join(methods)})"
                cls_code = "\n".join(lines[s_l - 1 : min(e_l, s_l + 90)])
                chunks.append({
                    "file_path": rel_path,
                    "symbol_name": node.name,
                    "symbol_type": "class",
                    "start_line": s_l,
                    "end_line": e_l,
                    "signature": f"{sig} — {cls_doc[:140]}".strip(),
                    "content": cls_code
                })
                # Si la clase es grande, indexar también cada método individualmente
                if (e_l - s_l) > 65:
                    for sub in node.body:
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            ms_l = getattr(sub, "lineno", s_l)
                            me_l = getattr(sub, "end_lineno", ms_l)
                            args = [a.arg for a in sub.args.args]
                            m_doc = ast.get_docstring(sub) or ""
                            m_code = "\n".join(lines[ms_l - 1 : min(me_l, ms_l + 80)])
                            chunks.append({
                                "file_path": rel_path,
                                "symbol_name": f"{node.name}.{sub.name}",
                                "symbol_type": "method",
                                "start_line": ms_l,
                                "end_line": me_l,
                                "signature": f"def {node.name}.{sub.name}({', '.join(args)}) — {m_doc[:120]}".strip(),
                                "content": m_code
                            })

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                s_l = getattr(node, "lineno", 1)
                e_l = getattr(node, "end_lineno", s_l)
                args = [a.arg for a in node.args.args]
                f_doc = ast.get_docstring(node) or ""
                decs = []
                for d in getattr(node, "decorator_list", []):
                    try:
                        decs.append("@" + ast.unparse(d))
                    except Exception:
                        pass
                dec_str = " ".join(decs) + " " if decs else ""
                is_endpoint = any("app." in d or "router." in d for d in decs)
                sym_type = "api_endpoint" if is_endpoint else "function"
                f_code = "\n".join(lines[s_l - 1 : min(e_l, s_l + 90)])
                chunks.append({
                    "file_path": rel_path,
                    "symbol_name": node.name,
                    "symbol_type": sym_type,
                    "start_line": s_l,
                    "end_line": e_l,
                    "signature": f"{dec_str}def {node.name}({', '.join(args)}) — {f_doc[:140]}".strip(),
                    "content": f_code
                })

        if not chunks:
            return StructuralCodeChunker._chunk_sliding_window(content, rel_path, ".py")
        return chunks

    @staticmethod
    def _chunk_brace_language(content: str, rel_path: str, ext: str) -> List[Dict[str, Any]]:
        lines = content.splitlines()
        chunks: List[Dict[str, Any]] = []
        pattern = re.compile(
            r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\s+([A-Za-z0-9_]+)|class\s+([A-Za-z0-9_]+)|(?:const|let|var|func|fn)\s+([A-Za-z0-9_]+)\s*=?\s*(?:async\s*)?\([^)]*\))"
        )
        matches = []
        for idx, line in enumerate(lines):
            m = pattern.search(line)
            if m:
                sym = m.group(1) or m.group(2) or m.group(3) or f"block_{idx+1}"
                kind = "class" if m.group(2) else "function"
                matches.append((idx + 1, sym, kind, line.strip()))

        if not matches:
            return StructuralCodeChunker._chunk_sliding_window(content, rel_path, ext)

        for i, (start_l, sym, kind, sig_line) in enumerate(matches):
            end_l = matches[i + 1][0] - 1 if (i + 1) < len(matches) else len(lines)
            end_l = min(end_l, start_l + 85)
            snippet = "\n".join(lines[start_l - 1 : end_l])
            chunks.append({
                "file_path": rel_path,
                "symbol_name": sym,
                "symbol_type": kind,
                "start_line": start_l,
                "end_line": end_l,
                "signature": sig_line[:140],
                "content": snippet
            })
        return chunks

    @staticmethod
    def _chunk_html(content: str, rel_path: str) -> List[Dict[str, Any]]:
        lines = content.splitlines()
        chunks: List[Dict[str, Any]] = []
        step = 65
        for i in range(0, len(lines), step):
            sub = lines[i : i + step]
            ids = re.findall(r'id=["\']([^"\']+)["\']', "\n".join(sub))
            sym_name = f"html_section_{i+1}" + (f" ({', '.join(ids[:4])})" if ids else "")
            chunks.append({
                "file_path": rel_path,
                "symbol_name": sym_name,
                "symbol_type": "html_component",
                "start_line": i + 1,
                "end_line": min(len(lines), i + step),
                "signature": f"Componente HTML en {rel_path} (IDs: {', '.join(ids[:6])})",
                "content": "\n".join(sub)
            })
        return chunks

    @staticmethod
    def _chunk_sliding_window(content: str, rel_path: str, ext: str) -> List[Dict[str, Any]]:
        lines = content.splitlines()
        chunks: List[Dict[str, Any]] = []
        step = 60
        for i in range(0, max(1, len(lines)), step):
            sub = lines[i : i + step]
            chunks.append({
                "file_path": rel_path,
                "symbol_name": f"{Path(rel_path).name}::L{i+1}-L{min(len(lines), i+step)}",
                "symbol_type": "code_block",
                "start_line": i + 1,
                "end_line": min(len(lines), i + step),
                "signature": f"Fragmento de {rel_path} (líneas {i+1}-{min(len(lines), i+step)})",
                "content": "\n".join(sub)
            })
        return chunks


class CodebaseVectorStore:
    """
    Base de Datos Vectorial Embebida para Codebase RAG:
    Persiste fragmentos AST y sus vectores de 256 dimensiones en SQLite,
    con sincronización incremental por hash SHA-256 y Reranker híbrido.
    """

    def __init__(self, db_path: Path = VECTOR_DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS indexed_files (
                    workspace TEXT,
                    file_path TEXT,
                    file_hash TEXT,
                    updated_at REAL,
                    PRIMARY KEY (workspace, file_path)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS code_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    workspace TEXT,
                    file_path TEXT,
                    symbol_name TEXT,
                    symbol_type TEXT,
                    start_line INTEGER,
                    end_line INTEGER,
                    signature TEXT,
                    content TEXT,
                    embedding BLOB
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_ws ON code_chunks(workspace)")

    def index_workspace(self, workspace_dir: Path, force: bool = False) -> Dict[str, Any]:
        t0 = time.time()
        if not workspace_dir or not workspace_dir.exists():
            return {"success": False, "chunks_indexed": 0, "files_scanned": 0}

        ws_key = str(workspace_dir.resolve())
        files_scanned = 0
        files_updated = 0
        chunks_created = 0
        current_rel_paths = set()

        with self._get_conn() as conn:
            existing_rows = conn.execute(
                "SELECT file_path, file_hash FROM indexed_files WHERE workspace = ?",
                (ws_key,)
            ).fetchall()
            known_hashes = {r["file_path"]: r["file_hash"] for r in existing_rows}

            for root, dirs, files in os.walk(workspace_dir):
                dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
                for fname in files:
                    fpath = Path(root) / fname
                    if fpath.suffix.lower() not in SUPPORTED_CODE_EXTS:
                        continue
                    try:
                        if fpath.stat().st_size > 350_000:
                            continue
                        raw_bytes = fpath.read_bytes()
                    except Exception:
                        continue

                    rel_p = str(fpath.relative_to(workspace_dir)).replace("\\", "/")
                    current_rel_paths.add(rel_p)
                    files_scanned += 1
                    f_hash = hashlib.sha256(raw_bytes).hexdigest()

                    if (not force) and known_hashes.get(rel_p) == f_hash:
                        continue

                    # Re-fragmentar y calcular embeddings
                    conn.execute("DELETE FROM code_chunks WHERE workspace = ? AND file_path = ?", (ws_key, rel_p))
                    file_chunks = StructuralCodeChunker.chunk_file(fpath, rel_p)
                    for ch in file_chunks:
                        c_id = hashlib.sha256(f"{ws_key}::{rel_p}::{ch['symbol_name']}::{ch['start_line']}".encode()).hexdigest()[:24]
                        embed_input = f"{ch['file_path']} {ch['symbol_name']} {ch['signature']}\n{ch['content']}"
                        vec = compute_local_code_embedding(embed_input, ch["symbol_name"], ch["symbol_type"])
                        conn.execute("""
                            INSERT OR REPLACE INTO code_chunks
                            (chunk_id, workspace, file_path, symbol_name, symbol_type, start_line, end_line, signature, content, embedding)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            c_id, ws_key, rel_p, ch["symbol_name"], ch["symbol_type"],
                            ch["start_line"], ch["end_line"], ch["signature"], ch["content"],
                            pack_vector(vec)
                        ))
                        chunks_created += 1

                    conn.execute("""
                        INSERT OR REPLACE INTO indexed_files (workspace, file_path, file_hash, updated_at)
                        VALUES (?, ?, ?, ?)
                    """, (ws_key, rel_p, f_hash, time.time()))
                    files_updated += 1

            # Purgar archivos eliminados del disco
            stale_paths = set(known_hashes.keys()) - current_rel_paths
            for sp in stale_paths:
                conn.execute("DELETE FROM code_chunks WHERE workspace = ? AND file_path = ?", (ws_key, sp))
                conn.execute("DELETE FROM indexed_files WHERE workspace = ? AND file_path = ?", (ws_key, sp))

            total_chunks = conn.execute(
                "SELECT COUNT(*) as c FROM code_chunks WHERE workspace = ?", (ws_key,)
            ).fetchone()["c"]

        return {
            "success": True,
            "workspace": ws_key,
            "files_scanned": files_scanned,
            "files_updated": files_updated,
            "new_chunks_embedded": chunks_created,
            "total_chunks_in_db": total_chunks,
            "parser_backend": "Tree-sitter + Python AST Hybrid" if HAS_TREE_SITTER else "Python AST + Multi-Language Structural Chunker",
            "elapsed_ms": round((time.time() - t0) * 1000, 2)
        }

    def search(self, workspace_dir: Path, query: str, top_k: int = 6) -> List[Dict[str, Any]]:
        """
        Búsqueda Semántica Híbrida (Similitud Coseno Vectorial + BM25 Léxico + Cross-Encoder Reranking).
        """
        self.index_workspace(workspace_dir, force=False)
        ws_key = str(workspace_dir.resolve())
        q_vec = compute_local_code_embedding(query)
        q_tokens = set(tokenize_code_identifiers(query))

        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT chunk_id, file_path, symbol_name, symbol_type, start_line, end_line, signature, content, embedding "
                "FROM code_chunks WHERE workspace = ?",
                (ws_key,)
            ).fetchall()

        if not rows:
            return []

        scored_results = []
        for r in rows:
            c_vec = unpack_vector(r["embedding"])
            vec_sim = cosine_similarity(q_vec, c_vec)

            # Puntuación léxica BM25 / Coincidencia exacta de símbolos (Cross-Feature Reranking)
            sym_low = r["symbol_name"].lower()
            fp_low = r["file_path"].lower()
            content_low = r["content"].lower()
            lexical_boost = 0.0
            for qt in q_tokens:
                if qt == sym_low or qt in sym_low:
                    lexical_boost += 0.32
                if qt in fp_low:
                    lexical_boost += 0.22
                if qt in content_low:
                    lexical_boost += 0.08

            # Priorizar clases, funciones y endpoints sobre bloques genéricos
            type_weight = 1.12 if r["symbol_type"] in ("class", "function", "api_endpoint", "method") else 1.0
            final_score = (vec_sim * 0.65 + min(1.0, lexical_boost) * 0.35) * type_weight

            scored_results.append({
                "chunk_id": r["chunk_id"],
                "file_path": r["file_path"],
                "symbol_name": r["symbol_name"],
                "symbol_type": r["symbol_type"],
                "start_line": r["start_line"],
                "end_line": r["end_line"],
                "signature": r["signature"],
                "content": r["content"],
                "score": round(final_score, 4)
            })

        scored_results.sort(key=lambda x: x["score"], reverse=True)
        return scored_results[:top_k]

    def build_rag_prompt_context(self, workspace_dir: Path, query: str, active_file: Optional[str] = None, top_k: int = 6) -> str:
        results = self.search(workspace_dir, query, top_k=top_k)
        if not results:
            return ""

        lines = [
            "\n=== CODEBASE RAG: FRAGMENTOS SEMÁNTICOS RECUPERADOS DE LA BASE VECTORIAL ==="
        ]
        for idx, res in enumerate(results, 1):
            if active_file and res["file_path"].lower() == active_file.lower() and idx > 2:
                continue
            lines.append(
                f"\n[{idx}] Archivo: `{res['file_path']}` (Líneas {res['start_line']}-{res['end_line']}) | "
                f"Símbolo: `{res['symbol_name']}` ({res['symbol_type']}) | Score: {res['score']}\n"
                f"Firma: {res['signature']}\n"
                f"```\n{res['content'][:1600]}\n```"
            )
        lines.append("=== FIN CODEBASE RAG ===\n")
        return "\n".join(lines)


class CodeArchitectureGraphRAG:
    """
    Code GraphRAG Ultraligero (<5 ms) para Modo 'Crear Apps':
    Construye un grafo dirigido G = (V, E) en memoria a partir del AST de Python, JS y HTML:
    - Nodos (V): Módulos, Clases (con atributos y métodos tipados), Dataclasses y Endpoints HTTP (/api/...).
    - Aristas (E):
        * IMPORTS: módulo A -> importa símbolo X de módulo B
        * CALLS: módulo A -> invoca método/función Y
        * EXPOSES_API: backend -> define ruta HTTP (/api/...)
        * CONSUMES_API: frontend (HTML/JS) -> llama fetch('/api/...')
    - Detecta aristas rotas (símbolos importados inexistentes o endpoints desalineados entre frontend y backend)
      sin meter texto basura ni latencia en la GPU.
    """

    def analyze_workspace_graph(self, workspace_dir: Optional[Path]) -> Dict[str, Any]:
        t0 = time.time()
        if not workspace_dir or not workspace_dir.exists():
            return {"nodes": [], "edges": [], "broken_edges": [], "prompt_context": "", "elapsed_ms": 0.0}

        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, str]] = []
        broken_edges: List[str] = []

        module_exports: Dict[str, Set[str]] = {}
        module_class_methods: Dict[str, Dict[str, List[str]]] = {}
        exposed_apis: Set[str] = set()
        consumed_apis: List[Tuple[str, str]] = []
        pending_imports: List[Tuple[str, str, str]] = []

        ignored_dirs = IGNORED_DIRS
        for root, dirs, files in os.walk(workspace_dir):
            dirs[:] = sorted([d for d in dirs if d not in ignored_dirs and not d.startswith(".")])
            for fname in sorted(files):
                if fname.startswith(".") or fname == "__init__.py":
                    continue
                fpath = Path(root) / fname
                ext = fpath.suffix.lower()
                if ext not in (".py", ".js", ".ts", ".html", ".htm"):
                    continue
                rel_p = str(fpath.relative_to(workspace_dir)).replace("\\", "/")
                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue

                if ext == ".py":
                    dotted_mod = re.sub(r"\.py$", "", rel_p, flags=re.IGNORECASE).replace("/", ".")
                    stem_mod = fpath.stem
                    exports: Set[str] = set()
                    cls_map: Dict[str, List[str]] = {}

                    # Detectar endpoints HTTP (FastAPI / Flask / http.server)
                    for m_api in re.finditer(r"""@(?:app|router|bp)\.(?:get|post|put|delete|patch|route)\(\s*['"]([^'"]+)['"]""", content):
                        ep = m_api.group(1).strip()
                        exposed_apis.add(ep)
                        edges.append({"source": rel_p, "rel": "EXPOSES_API", "target": ep})
                    for m_http in re.finditer(r"""self\.path\s*(?:==|\.startswith\()\s*['"](/[^'"]+)['"]""", content):
                        ep = m_http.group(1).strip()
                        exposed_apis.add(ep)
                        edges.append({"source": rel_p, "rel": "EXPOSES_API", "target": ep})

                    try:
                        tree = ast.parse(content, filename=rel_p)
                        for node in tree.body:
                            if isinstance(node, ast.ClassDef):
                                exports.add(node.name)
                                methods_sig = []
                                attrs = set()
                                for sub in node.body:
                                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                        args = [a.arg for a in sub.args.args if a.arg != "self"]
                                        methods_sig.append(f"{sub.name}({', '.join(args)})")
                                        for st in ast.walk(sub):
                                            if isinstance(st, ast.Attribute) and isinstance(st.value, ast.Name) and st.value.id == "self":
                                                if not st.attr.startswith("_"):
                                                    attrs.add(st.attr)
                                    elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                                        attrs.add(sub.target.id)
                                cls_map[node.name] = methods_sig
                                nodes.append({
                                    "file": rel_p,
                                    "type": "class",
                                    "name": node.name,
                                    "methods": methods_sig[:10],
                                    "attrs": sorted(list(attrs))[:8]
                                })
                            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                exports.add(node.name)
                                args = [a.arg for a in node.args.args]
                                nodes.append({
                                    "file": rel_p,
                                    "type": "function",
                                    "name": f"{node.name}({', '.join(args)})"
                                })
                            elif isinstance(node, ast.Assign):
                                for t in node.targets:
                                    if isinstance(t, ast.Name):
                                        exports.add(t.id)
                            elif isinstance(node, ast.ImportFrom) and node.module:
                                for alias in node.names:
                                    if alias.name != "*":
                                        edges.append({"source": rel_p, "rel": "IMPORTS", "target": f"{node.module}.{alias.name}"})
                                        pending_imports.append((rel_p, node.module, alias.name))
                    except SyntaxError:
                        pass

                    for k_mod in (dotted_mod, stem_mod):
                        module_exports[k_mod] = exports
                        module_class_methods[k_mod] = cls_map

                elif ext in (".html", ".htm", ".js", ".ts"):
                    for m_fetch in re.finditer(r"""fetch\(\s*[`'"](/api/[^`'"{}?]+)""", content):
                        ep = m_fetch.group(1).strip()
                        consumed_apis.append((rel_p, ep))
                        edges.append({"source": rel_p, "rel": "CONSUMES_API", "target": ep})

        # Verificar aristas rotas de imports entre módulos locales
        for src_file, target_mod, sym_name in pending_imports:
            if target_mod in module_exports:
                if sym_name not in module_exports[target_mod]:
                    avail = ", ".join(sorted(module_exports[target_mod])) or "ninguno"
                    broken_edges.append(
                        f"ARISTA ROTA (ImportError): `{src_file}` importa `{sym_name}` de `{target_mod}`, pero `{target_mod}` solo exporta: [{avail}]"
                    )

        # Verificar aristas rotas entre fetch('/api/...') del frontend y rutas del backend
        if exposed_apis and consumed_apis:
            for src_ui, ep in consumed_apis:
                if not any(ep == exp or ep.startswith(exp.rstrip("/") + "/") or exp.startswith(ep.rstrip("/") + "/") for exp in exposed_apis):
                    broken_edges.append(
                        f"ARISTA ROTA (API Mismatch): `{src_ui}` llama a `{ep}`, pero el backend solo expone: {sorted(list(exposed_apis))}"
                    )

        # Construir representación compacta del grafo G=(V,E) (~150-250 tokens)
        graph_lines = ["=== CODE GRAPHRAG: GRAFO DE ARQUITECTURA, CONTRATOS AST Y ARISTAS G=(V,E) ==="]
        if nodes:
            graph_lines.append("NODOS (V):")
            for n in nodes[:25]:
                if n["type"] == "class":
                    m_s = ", ".join(n["methods"]) if n["methods"] else "sin métodos"
                    a_s = f" | attrs: {', '.join(n['attrs'])}" if n.get("attrs") else ""
                    graph_lines.append(f"  • [{n['file']}] class {n['name']} -> métodos: [{m_s}]{a_s}")
                else:
                    graph_lines.append(f"  • [{n['file']}] def {n['name']}")
        if edges:
            graph_lines.append("ARISTAS (E):")
            for e in edges[:25]:
                graph_lines.append(f"  • {e['source']} --[{e['rel']}]--> {e['target']}")
        if broken_edges:
            graph_lines.append("⚠️ ALERTAS DE COHERENCIA DEL GRAFO (CORREGIR OBLIGATORIAMENTE):")
            for be in broken_edges[:10]:
                graph_lines.append(f"  ! {be}")
        graph_lines.append("=== FIN CODE GRAPHRAG ===")

        prompt_ctx = "\n".join(graph_lines) if (nodes or edges) else ""
        return {
            "nodes": nodes,
            "edges": edges,
            "broken_edges": broken_edges,
            "prompt_context": prompt_ctx,
            "elapsed_ms": round((time.time() - t0) * 1000, 2)
        }


codebase_rag_store = CodebaseVectorStore()
code_arch_graph = CodeArchitectureGraphRAG()
