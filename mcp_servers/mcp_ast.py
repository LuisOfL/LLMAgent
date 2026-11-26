# -*- coding: utf-8 -*-
"""
mcp_servers/mcp_ast.py — Servidor MCP #2 (MCP-AST): Esqueleto de Repo, Lectura y Parcheo Quirúrgico AST.
"""

import ast
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from mcp_servers._rpc_base import run_stdio_mcp_server
from core.cloud_workers import extract_module_signatures

TOOLS = [
    {
        "name": "get_repo_skeleton",
        "description": "Devuelve el esqueleto compacto del repositorio (archivos, símbolos AST, IDs DOM y assets) optimizado para KV-Cache.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_dir": {"type": "string"},
                "max_files": {"type": "integer", "default": 25}
            },
            "required": ["workspace_dir"]
        },
    },
    {
        "name": "read_symbol",
        "description": "Lee únicamente el código exacto de una clase o función específica usando AST (ahorra tokens).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_dir": {"type": "string"},
                "file_path": {"type": "string"},
                "symbol_name": {"type": "string"}
            },
            "required": ["workspace_dir", "file_path", "symbol_name"]
        },
    },
    {
        "name": "patch_symbol",
        "description": "Reemplaza quirúrgicamente una función o clase Python usando líneas AST verificando sintaxis antes de guardar.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_dir": {"type": "string"},
                "file_path": {"type": "string"},
                "symbol_name": {"type": "string"},
                "new_source": {"type": "string"}
            },
            "required": ["workspace_dir", "file_path", "symbol_name", "new_source"]
        },
    },
    {
        "name": "write_module",
        "description": "Escribe un archivo completo de forma segura dentro del workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_dir": {"type": "string"},
                "file_path": {"type": "string"},
                "content": {"type": "string"}
            },
            "required": ["workspace_dir", "file_path", "content"]
        },
    },
]

IGNORED_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".antigravity_rag", ".audit", "engine", "models"}


def _safe_target(ws: Path, rel_path: str) -> Path:
    target = (ws / rel_path.replace("\\", "/").lstrip("/")).resolve()
    if not str(target).startswith(str(ws)):
        raise PermissionError(f"Ruta fuera del workspace: {rel_path}")
    return target


def _get_repo_skeleton(args: Dict[str, Any]) -> Dict[str, Any]:
    ws = Path(str(args.get("workspace_dir", "."))).resolve()
    max_files = int(args.get("max_files") or 25)
    skeleton_lines: List[str] = []
    assets: List[str] = []
    count = 0

    for root, dirs, files in os.walk(ws):
        dirs[:] = sorted([d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")])
        for fname in sorted(files):
            if fname.startswith("."):
                continue
            p = Path(root) / fname
            rel = str(p.relative_to(ws)).replace("\\", "/")
            ext = p.suffix.lower()
            if ext in (".glb", ".gltf", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".mp3", ".mp4", ".csv", ".pdf"):
                assets.append(rel)
                continue
            if ext in (".py", ".js", ".html", ".htm", ".css") and count < max_files:
                try:
                    sig = extract_module_signatures(rel, p.read_text(encoding="utf-8", errors="replace"))
                    skeleton_lines.append(sig or f"- `{rel}`")
                    count += 1
                except Exception:
                    pass

    return {
        "success": True,
        "skeleton": "\n".join(skeleton_lines),
        "assets": assets[:20],
        "files_scanned": count,
    }


def _find_python_symbol_node(tree: ast.AST, symbol_name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol_name:
            return node
    return None


def _read_symbol(args: Dict[str, Any]) -> Dict[str, Any]:
    ws = Path(str(args.get("workspace_dir", "."))).resolve()
    rel = str(args.get("file_path", ""))
    sym = str(args.get("symbol_name", "")).strip()
    target = _safe_target(ws, rel)
    if not target.exists():
        return {"success": False, "error": f"Archivo no encontrado: {rel}"}
    src = target.read_text(encoding="utf-8", errors="replace")
    lines = src.splitlines()

    if target.suffix.lower() == ".py":
        tree = ast.parse(src)
        node = _find_python_symbol_node(tree, sym)
        if not node:
            return {"success": False, "error": f"Símbolo '{sym}' no encontrado en {rel}"}
        start = max(1, node.lineno)
        end = getattr(node, "end_lineno", start)
        snippet = "\n".join(lines[start - 1 : end])
        return {"success": True, "file_path": rel, "symbol": sym, "start_line": start, "end_line": end, "source": snippet}

    # Respaldo regex para JS
    m = re.search(rf"(?:function\s+{re.escape(sym)}\s*\([^)]*\)|class\s+{re.escape(sym)}\b)\s*\{{", src)
    if not m:
        return {"success": False, "error": f"Símbolo '{sym}' no encontrado en {rel}"}
    start_idx = m.start()
    return {"success": True, "file_path": rel, "symbol": sym, "source": src[start_idx : start_idx + 2200]}


_PLACEHOLDER_PATTERNS = [
    (r"//\s*\.\.\.", "elipsis '// ...'"),
    (r"//\s*(?:rest of|resto del c[oó]digo|c[oó]digo restante|implementaci[oó]n aqu[ií]|todo\b|fixme\b)", "comentario placeholder/TODO en JS/CSS"),
    (r"/\*\s*\.\.\.\s*\*/", "elipsis '/* ... */'"),
    (r"#\s*(?:\.\.\.|rest of|resto del c[oó]digo|todo\b|fixme\b|placeholder)", "comentario placeholder/TODO en Python"),
    (r"\braise\s+NotImplementedError\b", "NotImplementedError sin implementar"),
]


def _detect_truncation_or_placeholders(content: str, suffix: str) -> str:
    """Detecta código truncado o placeholders para rechazar la escritura inmediatamente."""
    for pat, label in _PLACEHOLDER_PATTERNS:
        if re.search(pat, content, re.IGNORECASE):
            return f"No se permiten elipsis ni código truncado ({label}). Escribe la implementación completa."
    if suffix == ".py":
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    body = [n for n in node.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str))]
                    if len(body) == 1:
                        only = body[0]
                        if isinstance(only, ast.Pass) or (isinstance(only, ast.Expr) and isinstance(only.value, ast.Constant) and only.value.value is Ellipsis):
                            return f"La función `{node.name}` contiene un cuerpo vacío (`pass`/`...`). Escribe la implementación completa."
        except SyntaxError:
            pass
    return ""


def _patch_symbol(args: Dict[str, Any]) -> Dict[str, Any]:
    ws = Path(str(args.get("workspace_dir", "."))).resolve()
    rel = str(args.get("file_path", ""))
    sym = str(args.get("symbol_name", "")).strip()
    new_src = str(args.get("new_source", ""))
    target = _safe_target(ws, rel)
    if not target.exists():
        return {"success": False, "error": f"Archivo no encontrado: {rel}"}
    if target.suffix.lower() != ".py":
        return {"success": False, "error": "patch_symbol AST soporta archivos .py; usa write_module para HTML/CSS/JS."}

    trunc_err = _detect_truncation_or_placeholders(new_src, ".py")
    if trunc_err:
        return {"success": False, "error": f"Error: {trunc_err}"}

    old_src = target.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(old_src)
    node = _find_python_symbol_node(tree, sym)
    if not node:
        return {"success": False, "error": f"Símbolo '{sym}' no encontrado en {rel}"}

    lines = old_src.splitlines()
    start = node.lineno - 1
    end = getattr(node, "end_lineno", node.lineno)
    indent = " " * getattr(node, "col_offset", 0)
    reindented = "\n".join((indent + ln if ln.strip() else ln) for ln in new_src.strip("\n").splitlines())
    updated = "\n".join(lines[:start] + [reindented] + lines[end:]) + "\n"

    # Validar sintaxis AST antes de escribir
    ast.parse(updated)
    target.write_text(updated, encoding="utf-8")
    return {"success": True, "file_path": rel, "symbol": sym, "lines_replaced": end - start, "refresh_workspace": True}


def _strip_markdown_fences(content: str, suffix: str) -> str:
    """Elimina cualquier marca Markdown (```html, ```js, ```) o <think> que pudiera filtrarse antes de guardar."""
    if not content:
        return content
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", content, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"^\s*```(?:file:[^\s]+|[a-zA-Z0-9_+\-]+)?[ \t]*\r?\n?", "", cleaned)
    cleaned = re.sub(r"\r?\n?[ \t]*```\s*$", "", cleaned)
    if suffix in (".html", ".htm"):
        m_html = re.search(r"(<!DOCTYPE\s+html|<html\b)", cleaned, re.IGNORECASE)
        if m_html and m_html.start() > 0 and "```" in cleaned[:m_html.start()]:
            cleaned = cleaned[m_html.start():]
    return cleaned.strip() + "\n" if cleaned.strip() else cleaned


def _write_module(args: Dict[str, Any]) -> Dict[str, Any]:
    ws = Path(str(args.get("workspace_dir", "."))).resolve()
    rel = str(args.get("file_path", ""))
    content = str(args.get("content", ""))
    allow_placeholders = bool(args.get("allow_placeholders", False))
    target = _safe_target(ws, rel)
    content = _strip_markdown_fences(content, target.suffix.lower())
    if not allow_placeholders:
        trunc_err = _detect_truncation_or_placeholders(content, target.suffix.lower())
        if trunc_err:
            return {"success": False, "error": f"Error: {trunc_err}"}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"success": True, "file_path": rel, "bytes": len(content.encode("utf-8")), "refresh_workspace": True}


if __name__ == "__main__":
    run_stdio_mcp_server(
        "mcp-ast",
        "1.0.0",
        TOOLS,
        {
            "get_repo_skeleton": _get_repo_skeleton,
            "read_symbol": _read_symbol,
            "patch_symbol": _patch_symbol,
            "write_module": _write_module,
        },
    )
