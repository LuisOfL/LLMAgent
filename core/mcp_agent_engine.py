# -*- coding: utf-8 -*-
"""
mcp_agent_engine.py - Salto al SOTA: Native Function Calling / Tool Use (Esquemas JSON)
y Protocolo MCP (Model Context Protocol JSON-RPC 2.0) con Bucle Agéntico ReAct / Plan-and-Execute.

Permite que tanto Qwen Local como AWS Bedrock decidan de forma autónoma qué herramienta invocar
(leer archivo, buscar en Codebase RAG vectorial, consultar Grafo STIX/CVE, correr en Sandbox aislado,
o aplicar parches quirúrgicos) y en qué orden.
"""

import os
import re
import ast
import json
import time
import py_compile
import urllib.request
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Callable

from core.codebase_rag import codebase_rag_store, code_arch_graph
from core.cybersecurity_kg import dynamic_cyber_graph, get_cybersecurity_graph_context
from core.terminal_engine import terminal_engine
from core.cloud_workers import audit_logger, auto_fix_common_python_issues
from core.mcp_client import mcp_client_manager


# ==============================================================================
# 1. DEFINICIÓN OFICIAL DE HERRAMIENTAS (JSON SCHEMA PARA MCP, QWEN Y BEDROCK)
# ==============================================================================

MCP_TOOLS_CATALOG: List[Dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "Lee el contenido de un archivo del repositorio del usuario con numeración o rango de líneas.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta relativa del archivo en el proyecto (ej. 'main.py')."},
                "start_line": {"type": "integer", "description": "Línea inicial (1-indexed, opcional).", "default": 1},
                "end_line": {"type": "integer", "description": "Línea final (opcional).", "default": 250}
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "Crea o sobrescribe un archivo en el proyecto verificando automáticamente su sintaxis AST.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta relativa del archivo a guardar."},
                "content": {"type": "string", "description": "Código fuente completo a escribir en el archivo."}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "apply_patch",
        "description": "Aplica un parche quirúrgico SEARCH/REPLACE sobre un archivo existente sin reescribirlo todo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta relativa del archivo a modificar."},
                "search_block": {"type": "string", "description": "Fragmento exacto de código original a buscar."},
                "replace_block": {"type": "string", "description": "Nuevo fragmento de código que reemplazará al original."}
            },
            "required": ["path", "search_block", "replace_block"]
        }
    },
    {
        "name": "list_workspace_files",
        "description": "Lista todos los archivos y directorios del repositorio activo con sus tamaños.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "search_codebase",
        "description": "Búsqueda semántica vectorial y estructural AST (Codebase RAG) sobre todas las funciones y clases del proyecto.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Consulta semántica o nombre de clase/función a buscar."},
                "top_k": {"type": "integer", "description": "Número de fragmentos lógicos a recuperar.", "default": 5}
            },
            "required": ["query"]
        }
    },
    {
        "name": "query_cyber_graph",
        "description": "Consulta la base de datos de grafos dinámica STIX 2.1 / MITRE ATT&CK / NVD CVE con BGE Reranker.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Técnica MITRE, herramienta, protocolo o identificador CVE (ej. 'CVE-2021-44228')."},
                "sync_nvd": {"type": "boolean", "description": "Si es True, sincroniza en vivo con la API NIST NVD 2.0.", "default": False}
            },
            "required": ["query"]
        }
    },
    {
        "name": "run_sandbox_command",
        "description": "Ejecuta un comando o prueba dentro del Sandbox Aislado (Contenedor Efímero Docker/Podman o Sandbox con AST Guard).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Comando a ejecutar dentro del entorno aislado (ej. 'python main.py --help')."},
                "timeout_s": {"type": "integer", "description": "Tiempo límite en segundos.", "default": 20}
            },
            "required": ["command"]
        }
    },
    {
        "name": "audit_dependencies",
        "description": "Analiza el AST de un archivo Python para detectar librerías de terceros faltantes e instalarlas con Pip.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Archivo Python a auditar (ej. 'main.py')."},
                "auto_install": {"type": "boolean", "description": "Instalar automáticamente paquetes faltantes.", "default": True}
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "inspect_webpage",
        "description": "Obtiene y analiza la estructura completa de una página web (URL pública, localhost o archivo .html local): título, metas, encabezados, formularios, enlaces, scripts y texto visible. Respeta el proxy anónimo Tor si está activo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "URL (https://..., http://127.0.0.1:...) o ruta relativa de un .html en el proyecto."}
            },
            "required": ["target"]
        }
    },
    {
        "name": "download_url",
        "description": "Descarga un archivo desde cualquier URL http/https directamente a la carpeta del proyecto abierto. Convierte enlaces de GitHub /blob/ a raw automáticamente y respeta el proxy anónimo Tor si está activo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL completa del archivo a descargar."},
                "filename": {"type": "string", "description": "Nombre o ruta relativa donde guardarlo (ej. 'modelo.glb' o 'assets/modelo.glb')."}
            },
            "required": ["url"]
        }
    },
    {
        "name": "search_web",
        "description": "Busca en internet enlaces reales y URLs directas de descarga (por ejemplo modelos .glb/.gltf, datasets, repositorios GitHub o documentación).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Consulta de búsqueda en internet."}
            },
            "required": ["query"]
        }
    },
    {
        "name": "compile_project",
        "description": "Compila y valida la sintaxis de un archivo o de todo el proyecto abierto (Python py_compile/AST, JSON, etc.) reportando errores exactos.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Archivo específico a compilar/validar, o vacío para validar todo el proyecto.", "default": ""}
            },
            "required": []
        }
    },
    {
        "name": "grep_search",
        "description": "Busca texto o expresiones regulares en todos los archivos del proyecto abierto devolviendo coincidencias exactas archivo:línea.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Texto o expresión regular a buscar (ej. 'def run' o 'fetch\\(')."},
                "max_matches": {"type": "integer", "description": "Número máximo de coincidencias.", "default": 25}
            },
            "required": ["pattern"]
        }
    },
    {
        "name": "inspect_code_graph",
        "description": "Construye el Code GraphRAG G=(V,E) del proyecto: devuelve todas las clases, métodos, rutas HTTP/API y aristas rotas (ImportError / API mismatch).",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "delete_file",
        "description": "Elimina un archivo obsoleto o temporal dentro de la carpeta del proyecto abierto.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta relativa del archivo a eliminar dentro del proyecto."}
            },
            "required": ["path"]
        }
    },
    {
        "name": "web_search",
        "description": "Busca información técnica actualizada y URLs reales verificadas en internet (DuckDuckGo / ddgs) respetando el proxy Tor si está activo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Consulta de búsqueda web."},
                "max_results": {"type": "integer", "description": "Número máximo de resultados.", "default": 4}
            },
            "required": ["query"]
        }
    }
]


class _StructuredHTMLAnalyzer(HTMLParser):
    """Extractor ligero de estructura DOM/HTML para que el agente entienda cualquier página web."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip_depth = 0
        self.metas: List[Dict[str, str]] = []
        self.headings: List[Dict[str, str]] = []
        self._cur_heading_tag: Optional[str] = None
        self._cur_heading_buf: List[str] = []
        self.links: List[Dict[str, str]] = []
        self.scripts: List[str] = []
        self.stylesheets: List[str] = []
        self.forms: List[Dict[str, Any]] = []
        self._cur_form: Optional[Dict[str, Any]] = None
        self.text_chunks: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]):
        t = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if t in ("script", "style", "noscript"):
            self._skip_depth += 1
            if t == "script" and attr_map.get("src"):
                self.scripts.append(attr_map["src"])
            return
        if t == "title":
            self._in_title = True
        elif t == "meta" and (attr_map.get("name") or attr_map.get("property")):
            self.metas.append({
                "name": attr_map.get("name") or attr_map.get("property", ""),
                "content": attr_map.get("content", "")[:160]
            })
        elif t in ("h1", "h2", "h3"):
            self._cur_heading_tag = t
            self._cur_heading_buf = []
        elif t == "a" and attr_map.get("href"):
            self.links.append({"href": attr_map["href"][:200]})
        elif t == "link" and "stylesheet" in attr_map.get("rel", "").lower() and attr_map.get("href"):
            self.stylesheets.append(attr_map["href"])
        elif t == "form":
            self._cur_form = {
                "method": attr_map.get("method", "GET").upper(),
                "action": attr_map.get("action", ""),
                "inputs": []
            }
        elif t in ("input", "textarea", "select", "button") and self._cur_form is not None:
            self._cur_form["inputs"].append({
                "tag": t,
                "type": attr_map.get("type", ""),
                "name": attr_map.get("name") or attr_map.get("id", "")
            })

    def handle_endtag(self, tag: str):
        t = tag.lower()
        if t in ("script", "style", "noscript") and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if t == "title":
            self._in_title = False
        elif t == self._cur_heading_tag:
            txt = " ".join("".join(self._cur_heading_buf).split())
            if txt:
                self.headings.append({"tag": t, "text": txt[:140]})
            self._cur_heading_tag = None
            self._cur_heading_buf = []
        elif t == "form" and self._cur_form is not None:
            self.forms.append(self._cur_form)
            self._cur_form = None

    def handle_data(self, data: str):
        if self._skip_depth > 0:
            return
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        if self._in_title:
            self.title = (self.title + " " + cleaned).strip()
        elif self._cur_heading_tag:
            self._cur_heading_buf.append(cleaned)
        else:
            self.text_chunks.append(cleaned)


def _get_active_proxy_url(url: str) -> Optional[str]:
    """Devuelve la URL del proxy Tor si está activo, evitando proxificar localhost."""
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
        return None
    proxy_cfg_path = Path(__file__).resolve().parent.parent / ".antigravity_proxy.json"
    if proxy_cfg_path.exists():
        try:
            import socket
            cfg = json.loads(proxy_cfg_path.read_text(encoding="utf-8"))
            if cfg.get("enabled") or cfg.get("auto_start"):
                p_url = cfg.get("proxy_url") or "socks5://127.0.0.1:9050"
                p_parsed = urllib.parse.urlparse(p_url)
                p_host = p_parsed.hostname or "127.0.0.1"
                p_port = p_parsed.port or 9050
                if p_host in ("127.0.0.1", "localhost"):
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.settimeout(0.15)
                        if s.connect_ex((p_host, p_port)) != 0:
                            return None
                return p_url
        except Exception:
            pass
    return None


_PUBLIC_ASSET_HOSTS = (
    "raw.githubusercontent.com",
    "github.com",
    "archive.org",
    "commons.wikimedia.org",
    "upload.wikimedia.org",
    "arxiv.org",
    "export.arxiv.org",
    "huggingface.co",
)


def _is_public_asset_url(url: str) -> bool:
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        return any(host == h or host.endswith("." + h) for h in _PUBLIC_ASSET_HOSTS)
    except Exception:
        return False


def _fetch_url_bytes(url: str, timeout: float = 30.0) -> Tuple[int, bytes, str]:
    """Descarga bytes de una URL mediante HTTPS directo con reintentos (sin forzar Tor en assets públicos)."""
    import ssl
    proxy_url = None if _is_public_asset_url(url) else _get_active_proxy_url(url)
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    headers = {"User-Agent": ua}
    if proxy_url:
        import primp
        client = primp.Client(proxy=proxy_url, timeout=timeout, verify=False)
        resp = client.get(url, headers=headers)
        return int(resp.status_code), bytes(resp.content), f"proxy:{proxy_url}"

    # 1. Intentar con certifi si está disponible
    ctx = None
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return int(getattr(resp, "status", 200)), resp.read(), "direct"
    except Exception as e:
        err_str = str(e).lower()
        if "ssl" not in err_str and "certificate" not in err_str:
            raise

    # 2. Fallback con primp (TLS BoringSSL con certs propios)
    try:
        import primp
        client = primp.Client(timeout=timeout)
        resp = client.get(url, headers=headers)
        return int(resp.status_code), bytes(resp.content), "direct:primp"
    except Exception:
        pass

    # 3. Último fallback sin verificación estricta de emisor local (Windows AV / proxy corporativo)
    unverified_ctx = ssl._create_unverified_context()
    with urllib.request.urlopen(req, timeout=timeout, context=unverified_ctx) as resp:
        return int(getattr(resp, "status", 200)), resp.read(), "direct:unverified-ssl"


_ES_TO_EN_FILE_TERMS = {
    "carro": ["car", "buggy", "truck", "vehicle"],
    "coche": ["car", "buggy", "truck", "vehicle"],
    "auto": ["car", "buggy", "truck", "vehicle"],
    "vehiculo": ["car", "buggy", "truck", "vehicle"],
    "camion": ["truck", "milktruck"],
    "casco": ["helmet", "damagedhelmet", "flighthelmet", "scifihelmet"],
    "pato": ["duck"],
    "zorro": ["fox"],
    "aguacate": ["avocado"],
    "linterna": ["lantern"],
    "camara": ["camera", "antiquecamera"],
    "zapato": ["shoe", "sneaker"],
    "tenis": ["shoe", "sneaker"],
    "silla": ["chair", "sheenchair"],
    "botella": ["bottle", "waterbottle"],
    "robot": ["brainstem", "cesiumman", "robot"],
    "persona": ["cesiumman", "human"],
    "humano": ["cesiumman", "human"],
    "avion": ["plane", "aircraft"],
    "barco": ["ship", "boat"],
    "espada": ["sword"],
    "casa": ["house", "building"],
    "arbol": ["tree", "plant"],
    "perro": ["dog"],
    "gato": ["cat"],
    "caja": ["box", "boxanimated"],
    "esfera": ["sphere", "metalroughspheres"],
    "radio": ["boombox"],
    "bocina": ["boombox"],
    "pez": ["fish", "barramundifish"],
    "pescado": ["fish", "barramundifish"],
    "corazon": ["heart"],
    "cerebro": ["brainstem"],
    "monstruo": ["monster"],
    "dragon": ["dragon"],
}


def _detect_requested_extension(query: str) -> str:
    """Detecta qué extensión de archivo pide descargar el usuario (ej. '.glb', '.mp4', '.mp3', '.pdf', '.png', '.csv', '.zip')."""
    q_low = (query or "").lower()
    known_exts = [
        "glb", "gltf", "obj", "fbx", "stl",
        "png", "jpg", "jpeg", "webp", "svg", "gif",
        "mp3", "wav", "ogg", "flac", "mp4", "webm", "ogv",
        "csv", "tsv", "json", "xml", "yaml", "yml", "pdf", "epub",
        "zip", "tar", "gz", "ttf", "otf", "woff2", "txt", "md", "py", "js", "html", "css"
    ]
    for ext in known_exts:
        if re.search(rf"(?:\.|archivo\s+|modelo\s+|imagen\s+|dataset\s+|en\s+|formato\s+){ext}\b", q_low) or re.search(rf"\b{ext}\b", q_low):
            return f".{ext}"
    if any(k in q_low for k in ("modelo 3d", "3d model", "objeto 3d")):
        return ".glb"
    if any(k in q_low for k in ("video", "pelicula", "película", "pelucla", "cortometraje", "clip", "animacion", "animación")):
        return ".mp4"
    if any(k in q_low for k in ("audio", "musica", "música", "cancion", "canción", "sonido", "efecto de sonido", "podcast")):
        return ".mp3"
    if any(k in q_low for k in ("paper", "libro", "manual", "documento", "articulo cientifico", "artículo científico", "ebook")):
        return ".pdf"
    if any(k in q_low for k in ("dataset", "conjunto de datos", "datos csv", "tabla de datos")):
        return ".csv"
    if any(k in q_low for k in ("imagen", "foto", "wallpaper", "fondo", "icono", "logo")):
        return ".png"
    if any(k in q_low for k in ("fuente", "tipografia", "tipografía", "font")):
        return ".ttf"
    if any(k in q_low for k in ("comprimido", "paquete zip", "repositorio zip")):
        return ".zip"
    return ""


def _expand_search_terms(query: str) -> List[str]:
    """Extrae palabras clave limpias y agrega traducciones ES->EN para encontrar assets reales."""
    stop = {
        "descarga", "descargar", "descargue", "baja", "bajar", "pon", "ponlo", "ponla", "poner",
        "aqui", "aca", "repo", "repositorio", "proyecto", "carpeta", "internet", "web",
        "archivo", "modelo", "algo", "como", "para", "este", "esta", "unos", "unas",
        "desde", "favor", "puedes", "quiero", "necesito", "formato", "tipo", "una", "uno",
        "the", "and", "for", "from", "with", "download", "file", "model",
        "pelicula", "pelucla", "video", "audio", "imagen", "foto", "libro", "documento", "dataset"
    }
    raw_words = re.findall(r"[a-z0-9áéíóúñ]{3,}", (query or "").lower())
    norm_map = str.maketrans("áéíóúñ", "aeioun")
    terms: List[str] = []
    for w in raw_words:
        wn = w.translate(norm_map)
        if wn in stop or wn in ("glb", "gltf", "png", "jpg", "jpeg", "csv", "json", "zip", "pdf", "mp3", "mp4", "wav", "svg"):
            continue
        if wn not in terms:
            terms.append(wn)
        if wn in _ES_TO_EN_FILE_TERMS:
            for en in _ES_TO_EN_FILE_TERMS[wn]:
                if en not in terms:
                    terms.append(en)
    return terms


def _normalize_raw_url(url: str) -> str:
    u = (url or "").strip()
    gh_blob = re.match(r"^https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)$", u, re.IGNORECASE)
    if gh_blob:
        owner, repo, branch, path = gh_blob.groups()
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    gh_repo = re.match(r"^https?://github\.com/([^/]+)/([^/?#]+)/?$", u, re.IGNORECASE)
    if gh_repo:
        owner, repo = gh_repo.groups()
        repo = repo[:-4] if repo.lower().endswith(".git") else repo
        return f"https://github.com/{owner}/{repo}/archive/refs/heads/main.zip"
    arxiv_abs = re.match(r"^https?://arxiv\.org/abs/([0-9v\.]+)$", u, re.IGNORECASE)
    if arxiv_abs:
        return f"https://arxiv.org/pdf/{arxiv_abs.group(1)}.pdf"
    return u


_MIME_TO_EXT = {
    "model/gltf-binary": ".glb",
    "model/gltf+json": ".gltf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/ogg": ".ogv",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/ogg": ".ogg",
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "application/json": ".json",
    "text/csv": ".csv",
    "text/plain": ".txt",
    "font/ttf": ".ttf",
    "font/otf": ".otf",
    "font/woff2": ".woff2",
}


def _stream_download_to_file(
    url: str,
    dest_path: Path,
    expected_ext: str = "",
    timeout: float = 90.0,
    max_bytes: int = 250 * 1024 * 1024,
) -> Dict[str, Any]:
    """
    Descarga cualquier archivo público en streaming directo a disco (sin saturar RAM),
    soportando SSL en Windows, proxy Tor, Content-Disposition, detección de MIME y validación de cabeceras.
    """
    import ssl
    proxy_url = None if _is_public_asset_url(url) else _get_active_proxy_url(url)
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    headers = {"User-Agent": ua}
    bin_exts = {
        ".glb", ".gltf", ".png", ".jpg", ".jpeg", ".webp", ".gif",
        ".zip", ".gz", ".tar", ".mp3", ".wav", ".ogg", ".flac",
        ".mp4", ".webm", ".ogv", ".pdf", ".epub", ".bin", ".gguf", ".ttf", ".otf", ".woff2"
    }

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = dest_path.with_suffix(dest_path.suffix + ".part")

    def _validate_head(head: bytes, suffix: str, content_type: str):
        suf = suffix.lower()
        ct = (content_type or "").lower()
        if suf in bin_exts:
            h_low = head[:512].lstrip().lower()
            if h_low.startswith(b"<!doctype html") or h_low.startswith(b"<html") or "text/html" in ct:
                raise Exception(f"La URL devolvió una página HTML en lugar del archivo '{suf}' (posiblemente requiere inicio de sesión o no es un enlace directo).")
            if suf == ".glb" and not head.startswith(b"glTF"):
                raise Exception("El archivo descargado no tiene cabecera binaria glTF válida.")
            if suf == ".pdf" and not head.lstrip().startswith(b"%PDF"):
                raise Exception("El archivo descargado no es un PDF válido.")

    try:
        if proxy_url:
            import primp
            client = primp.Client(proxy=proxy_url, timeout=timeout, verify=False)
            resp = client.get(url, headers=headers)
            if int(resp.status_code) >= 400:
                raise Exception(f"HTTP {resp.status_code}")
            raw_b = bytes(resp.content)
            if len(raw_b) > max_bytes:
                raise Exception(f"El archivo excede el límite automático de {max_bytes // (1024*1024)} MB.")
            ct = resp.headers.get("content-type", "") if hasattr(resp, "headers") else ""
            _validate_head(raw_b[:1024], expected_ext or dest_path.suffix, ct)
            dest_path.write_bytes(raw_b)
            return {"size_bytes": len(raw_b), "content_type": ct, "transport": f"proxy:{proxy_url}", "final_path": dest_path}

        try:
            import certifi
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ssl_ctx = ssl.create_default_context()

        req = urllib.request.Request(url, headers=headers)

        def _do_stream(ctx, tr_name: str):
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                status_c = int(getattr(resp, "status", 200))
                if status_c >= 400:
                    raise Exception(f"HTTP {status_c}")
                ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                cl = resp.headers.get("Content-Length")
                if cl and cl.isdigit() and int(cl) > max_bytes:
                    raise Exception(f"Archivo demasiado grande ({int(cl) // (1024*1024)} MB > límite de {max_bytes // (1024*1024)} MB).")

                # Inferir nombre/extensión si venía genérico
                final_target = dest_path
                if final_target.name == "archivo_descargado.txt":
                    cd = resp.headers.get("Content-Disposition") or ""
                    m_cd = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";\r\n]+)"?', cd, re.IGNORECASE)
                    if m_cd:
                        cd_name = Path(urllib.parse.unquote(m_cd.group(1).strip())).name
                        if cd_name:
                            final_target = final_target.parent / cd_name
                    elif ct in _MIME_TO_EXT:
                        final_target = final_target.with_suffix(_MIME_TO_EXT[ct])

                first_chunk = resp.read(262144)
                if not first_chunk:
                    raise Exception("La URL devolvió 0 bytes.")
                _validate_head(first_chunk[:1024], expected_ext or final_target.suffix, ct)

                total = len(first_chunk)
                with open(part_path, "wb") as f_out:
                    f_out.write(first_chunk)
                    while True:
                        chunk = resp.read(262144)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            raise Exception(f"Descarga detenida: excede el límite automático de {max_bytes // (1024*1024)} MB.")
                        f_out.write(chunk)

                if final_target.exists():
                    final_target.unlink()
                part_path.replace(final_target)
                return {"size_bytes": total, "content_type": ct, "transport": tr_name, "final_path": final_target}

        try:
            return _do_stream(ssl_ctx, "direct")
        except Exception as e:
            err_s = str(e).lower()
            if "ssl" not in err_s and "certificate" not in err_s:
                raise
            return _do_stream(ssl._create_unverified_context(), "direct:unverified-ssl")
    finally:
        if part_path.exists():
            try:
                part_path.unlink()
            except Exception:
                pass


def _search_internet_archive(query_terms: List[str], ext: str, max_items: int = 4) -> List[Dict[str, str]]:
    """Busca archivos públicos y de dominio público en Internet Archive (archive.org) sin login."""
    out: List[Dict[str, str]] = []
    q_text = " ".join(query_terms[:4]).strip()
    if not q_text:
        return out

    media_filter = ""
    if ext in (".mp4", ".webm", ".ogv"):
        media_filter = " AND mediatype:movies"
    elif ext in (".mp3", ".wav", ".ogg", ".flac"):
        media_filter = " AND mediatype:audio"
    elif ext in (".pdf", ".epub", ".txt"):
        media_filter = " AND mediatype:texts"
    elif ext in (".csv", ".json", ".xml"):
        media_filter = " AND mediatype:data"
    elif ext in (".zip",):
        media_filter = " AND (mediatype:software OR mediatype:data)"

    adv_q = urllib.parse.quote_plus(f"({q_text}){media_filter}")
    adv_url = f"https://archive.org/advancedsearch.php?q={adv_q}&fl[]=identifier&fl[]=title&fl[]=mediatype&sort[]=downloads+desc&rows=4&page=1&output=json"
    try:
        st, raw, _ = _fetch_url_bytes(adv_url, timeout=14.0)
        if st >= 400:
            return out
        docs = ((json.loads(raw.decode("utf-8", errors="replace")).get("response") or {}).get("docs")) or []
        for doc in docs[:3]:
            ident = doc.get("identifier")
            title = doc.get("title") or ident
            if not ident:
                continue
            st_m, raw_m, _ = _fetch_url_bytes(f"https://archive.org/metadata/{ident}/files", timeout=12.0)
            if st_m >= 400:
                continue
            files = json.loads(raw_m.decode("utf-8", errors="replace")).get("result") or []
            # Filtrar archivos públicos que coincidan con la extensión y <= 120 MB
            candidates = []
            for f in files:
                if not isinstance(f, dict):
                    continue
                fname = str(f.get("name") or "")
                if not fname or fname.startswith("__") or "_meta." in fname:
                    continue
                fsize = int(f.get("size") or 0) if str(f.get("size") or "").isdigit() else 0
                if fsize <= 0 or fsize > 120 * 1024 * 1024:
                    continue
                if ext and not fname.lower().endswith(ext):
                    continue
                if not ext and Path(fname).suffix.lower() not in (".mp4", ".mp3", ".pdf", ".zip", ".csv", ".json", ".png", ".jpg", ".txt", ".glb"):
                    continue
                candidates.append((fsize, fname))
            # Preferir un archivo compacto pero real (evitar thumbnails diminutos)
            candidates.sort(key=lambda x: x[0])
            if candidates:
                best_size, best_name = candidates[0]
                dl_url = f"https://archive.org/download/{urllib.parse.quote(ident)}/{urllib.parse.quote(best_name)}"
                out.append({
                    "title": f"Internet Archive: {title} ({Path(best_name).name})",
                    "url": dl_url,
                    "filename": Path(best_name).name,
                    "snippet": f"Archivo público de Internet Archive ({round(best_size / (1024 * 1024), 2)} MB)."
                })
                if len(out) >= max_items:
                    break
    except Exception:
        pass
    return out


def _find_verified_download_urls(query: str, target_ext: str = "", max_results: int = 6) -> List[Dict[str, str]]:
    """
    Buscador universal de archivos públicos descargables en internet (sin login ni permisos):
    - 3D (.glb/.gltf): Khronos glTF-Sample-Assets (model-index.json directo)
    - Imágenes (.png/.jpg/.svg/.webp): Wikimedia Commons API
    - Papers/PDFs (.pdf): arXiv API + Internet Archive
    - Videos/Audio/Libros/Datasets/ZIPs: Internet Archive (archive.org)
    - General: búsqueda web (DDGS) + extracción de enlaces directos en páginas HTML
    """
    ext = (target_ext or _detect_requested_extension(query)).lower()
    terms = _expand_search_terms(query)
    results: List[Dict[str, str]] = []
    seen_urls = set()

    def _add(title: str, url: str, filename: str, snippet: str):
        u = _normalize_raw_url(url)
        if not u or u in seen_urls:
            return
        seen_urls.add(u)
        results.append({"title": title, "url": u, "filename": filename, "snippet": snippet})
        if not u or u in seen_urls:
            return
        seen_urls.add(u)
        results.append({"title": title, "url": u, "filename": filename, "snippet": snippet})

    # 1. Modelos 3D (.glb / .gltf): catálogo oficial Khronos glTF-Sample-Assets vía raw.githubusercontent.com (sin límite de API)
    if ext in (".glb", ".gltf"):
        try:
            idx_url = "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/model-index.json"
            st, raw, _ = _fetch_url_bytes(idx_url, timeout=18.0)
            if st < 400:
                catalog = json.loads(raw.decode("utf-8", errors="replace"))
                scored = []
                for item in (catalog if isinstance(catalog, list) else []):
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name", "")
                    variants = item.get("variants") or {}
                    glb_file = variants.get("glTF-Binary")
                    if not name or not glb_file:
                        continue
                    haystack = " ".join([
                        name.lower(),
                        str(item.get("label", "")).lower(),
                        " ".join(str(t).lower() for t in (item.get("tags") or []))
                    ])
                    score = 0
                    for t in terms:
                        if t == name.lower():
                            score += 10
                        elif t in name.lower():
                            score += 5
                        elif t in haystack:
                            score += 2
                    scored.append((score, name, glb_file, item.get("label", name)))
                scored.sort(key=lambda x: (-x[0], x[1]))
                picks = [s for s in scored if s[0] > 0][:5] if any(s[0] > 0 for s in scored) else scored[:5]
                for _, m_name, glb_file, label in picks:
                    raw_url = f"https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/{m_name}/glTF-Binary/{glb_file}"
                    _add(
                        f"Khronos 3D Model: {label} ({glb_file})",
                        raw_url,
                        glb_file,
                        f"Modelo 3D oficial KhronosGroup en formato binario .glb listo para descarga directa."
                    )
        except Exception:
            pass

    # 2. Imágenes (.png/.jpg/.jpeg/.webp/.svg): Wikimedia Commons API (sin API key, URLs directas)
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".svg") and len(results) < max_results:
        try:
            q_img = " ".join(terms[:4]) or "technology"
            wm_url = (
                "https://commons.wikimedia.org/w/api.php?action=query&generator=search"
                f"&gsrsearch={urllib.parse.quote_plus(q_img)}&gsrnamespace=6"
                "&prop=imageinfo&iiprop=url|mime|size&format=json&gsrlimit=6"
            )
            st, raw, _ = _fetch_url_bytes(wm_url, timeout=14.0)
            if st < 400:
                data = json.loads(raw.decode("utf-8", errors="replace"))
                pages = (data.get("query") or {}).get("pages") or {}
                for page in pages.values():
                    infos = page.get("imageinfo") or []
                    if not infos:
                        continue
                    u = infos[0].get("url", "")
                    if not u:
                        continue
                    fname = urllib.parse.unquote(Path(urllib.parse.urlparse(u).path).name)
                    if fname:
                        _add(page.get("title", fname), u, fname, f"Imagen directa de Wikimedia Commons ({infos[0].get('mime', '')}).")
        except Exception:
            pass

    # 3. Papers / PDFs: arXiv API (PDFs directos sin login)
    if ext == ".pdf" and len(results) < max_results and terms:
        try:
            q_ax = urllib.parse.quote_plus(" ".join(terms[:4]))
            ax_url = f"https://export.arxiv.org/api/query?search_query=all:{q_ax}&start=0&max_results=4"
            st, raw, _ = _fetch_url_bytes(ax_url, timeout=14.0)
            if st < 400:
                xml_txt = raw.decode("utf-8", errors="replace")
                entries = re.findall(r"<entry>([\s\S]*?)</entry>", xml_txt)
                for ent in entries[:3]:
                    m_id = re.search(r"<id>https?://arxiv\.org/abs/([^<]+)</id>", ent)
                    m_t = re.search(r"<title>([\s\S]*?)</title>", ent)
                    if m_id:
                        aid = m_id.group(1).strip()
                        atitle = re.sub(r"\s+", " ", m_t.group(1)).strip() if m_t else aid
                        safe_fname = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", f"arxiv_{aid}.pdf")
                        _add(f"arXiv Paper: {atitle}", f"https://arxiv.org/pdf/{aid}.pdf", safe_fname, "Documento PDF público de arXiv.")
        except Exception:
            pass

    # 4. Internet Archive (videos, audio, libros/PDFs, datasets, zips de dominio público sin login)
    if ext in ("", ".mp4", ".webm", ".ogv", ".mp3", ".wav", ".ogg", ".flac", ".pdf", ".epub", ".txt", ".csv", ".json", ".zip") and len(results) < max_results and terms:
        for ia_item in _search_internet_archive(terms, ext, max_items=3):
            _add(ia_item["title"], ia_item["url"], ia_item["filename"], ia_item["snippet"])

    # 5. Búsqueda web general (DDGS) + extracción de enlaces directos con la extensión pedida
    if len(results) < max_results:
        try:
            from ddgs import DDGS
            proxy_arg = _get_active_proxy_url()
            base_q = " ".join(terms[:5]) if terms else query
            if ext in (".csv", ".json", ".py", ".js", ".glb", ".gltf", ".ttf", ".zip"):
                search_q = f"{base_q} {ext} raw.githubusercontent.com OR github"
            elif ext:
                search_q = f"{base_q} filetype:{ext.lstrip('.')}"
            else:
                search_q = base_q
            with DDGS(proxy=proxy_arg, timeout=12.0 if proxy_arg else 7.0) as ddgs:
                ddg_hits = list(ddgs.text(search_q, max_results=6))
            for r in ddg_hits:
                href = _normalize_raw_url(r.get("href", ""))
                title = r.get("title", "")
                body = r.get("body", "")
                if not href:
                    continue
                parsed_name = urllib.parse.unquote(Path(urllib.parse.urlparse(href).path).name)
                if ext and href.lower().split("?")[0].endswith(ext):
                    _add(title or parsed_name, href, parsed_name or f"archivo{ext}", body)
                elif not ext and Path(parsed_name).suffix.lower() in (".pdf", ".csv", ".json", ".zip", ".png", ".jpg", ".mp3", ".mp4", ".glb", ".txt", ".py", ".js"):
                    _add(title or parsed_name, href, parsed_name, body)
                elif len(results) < max_results:
                    # Extraer enlaces directos de descarga dentro de la página HTML (href / src / source)
                    try:
                        st_p, raw_p, _ = _fetch_url_bytes(href, timeout=8.0)
                        if st_p < 400 and raw_p:
                            html_p = raw_p[:180000].decode("utf-8", errors="ignore")
                            ext_pat = re.escape(ext) if ext else r"\.(?:glb|gltf|png|jpg|jpeg|webp|svg|mp4|webm|mp3|wav|pdf|csv|json|zip|ttf)"
                            direct_links = re.findall(rf"https?://[^\s\"'<>]+{ext_pat}(?:\?[^\s\"'<>]*)?", html_p, re.IGNORECASE)
                            for dl in direct_links[:2]:
                                dl_norm = _normalize_raw_url(dl)
                                dl_name = urllib.parse.unquote(Path(urllib.parse.urlparse(dl_norm).path).name)
                                _add(f"{title} ({dl_name})", dl_norm, dl_name, f"Enlace directo extraído de {href}")
                    except Exception:
                        pass
        except Exception:
            pass

    return results[:max_results]


def get_openai_compatible_tools(allowed_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Convierte las herramientas internas + herramientas descubiertas en servidores MCP externos (JSON-RPC 2.0)."""
    allowed_set = set(allowed_names) if allowed_names else None
    builtin = [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["inputSchema"]
            }
        }
        for t in MCP_TOOLS_CATALOG
        if allowed_set is None or t["name"] in allowed_set
    ]
    # Incorporar herramientas reales descubiertas por el cliente MCP (stdio / HTTP)
    external = mcp_client_manager.list_external_openai_tools()
    return builtin + external


def classify_tool_profile(user_query: str) -> str:
    """Clasifica la intención del usuario para filtrar herramientas (Tool Masking) y elegir few-shots dinámicos."""
    q = (user_query or "").strip().lower()
    if any(w in q for w in ("descarga", "descargar", "baja ", "bajar ", "download", "http://", "https://", ".glb", ".gltf", ".mp4", ".mp3", ".pdf")):
        return "download"
    if any(w in q for w in ("error", "bug", "falla", "arregla", "corrige", "fix", "parche", "edita", "modifica", "cambia", "actualiza", "compila", "ejecuta")):
        return "edit_debug"
    if any(w in q for w in ("explica", "analiza", "que hace", "qué hace", "donde esta", "dónde está", "estructura", "arquitectura", "revisa", "lista")):
        return "explore"
    return "build_app"


def get_openai_compatible_tools_for_task(user_query: str) -> List[Dict[str, Any]]:
    """
    Tool Masking por intención: expone solo las 4-7 herramientas relevantes al modelo 7B-9B
    para reducir ruido de contexto y aumentar la precisión de Function Calling sin fine-tuning.
    """
    profile = classify_tool_profile(user_query)
    profile_tools = {
        "download": ["search_web", "download_url", "inspect_webpage", "list_workspace_files"],
        "edit_debug": ["read_file", "apply_patch", "write_file", "grep_search", "compile_project", "run_sandbox_command"],
        "explore": ["list_workspace_files", "search_codebase", "grep_search", "read_file", "inspect_code_graph"],
        "build_app": ["list_workspace_files", "read_file", "write_file", "apply_patch", "compile_project", "download_url", "search_web"],
    }
    return get_openai_compatible_tools(profile_tools.get(profile))


def get_dynamic_agent_few_shots(user_query: str) -> str:
    """
    In-Context Learning (Few-Shot dinámico por intención): inyecta 2 ejemplos breves de
    comportamiento agéntico (incluyendo manejo honesto de errores) sin necesidad de fine-tuning.
    """
    profile = classify_tool_profile(user_query)
    if profile == "download":
        return (
            "\n[EJEMPLOS AGÉNTICOS (FEW-SHOT — DESCARGA)]:\n"
            "Ejemplo 1 (éxito):\n"
            "Usuario: Descarga un modelo glb de un casco y ponlo en el repo.\n"
            "Asistente: -> llama a `search_web(query='helmet glb')` -> recibe URL real `https://raw.githubusercontent.com/.../DamagedHelmet.glb` "
            "-> llama a `download_url(url='...', filename='DamagedHelmet.glb')` -> recibe `success: true, size_bytes: 3774200` "
            "-> responde: 'Listo, descargué `DamagedHelmet.glb` (3.77 MB) en tu proyecto.'\n"
            "Ejemplo 2 (fallo honesto — NUNCA mentir):\n"
            "Usuario: Descarga un archivo privado que requiere login.\n"
            "Asistente: -> llama a `download_url(...)` -> recibe `success: false, error: 'HTTP 403 / requiere login'` "
            "-> responde: 'No pude descargarlo porque la fuente devolvió HTTP 403 / requiere inicio de sesión. No se guardó ningún archivo.'\n"
        )
    if profile == "edit_debug":
        return (
            "\n[EJEMPLOS AGÉNTICOS (FEW-SHOT — EDICIÓN Y DEPURACIÓN)]:\n"
            "Ejemplo 1:\n"
            "Usuario: Corrige el error de sintaxis en app.py.\n"
            "Asistente: -> llama a `read_file(file_path='app.py')` -> identifica la línea exacta -> llama a `apply_patch(...)` "
            "-> llama a `compile_project(file_path='app.py')` -> confirma que compiló con 0 errores.\n"
            "Ejemplo 2 (error de herramienta):\n"
            "Si `apply_patch` devuelve `success: false`, vuelve a leer el archivo con `read_file` para usar el texto exacto antes de reintentar.\n"
        )
    if profile == "explore":
        return (
            "\n[EJEMPLOS AGÉNTICOS (FEW-SHOT — ANÁLISIS DE CÓDIGO)]:\n"
            "Ejemplo 1:\n"
            "Usuario: ¿Cómo funciona el servidor?\n"
            "Asistente: -> llama a `list_workspace_files()` y `read_file(file_path='server.py')` "
            "-> responde citando únicamente archivos, funciones y rutas realmente observadas.\n"
        )
    return (
        "\n[EJEMPLOS AGÉNTICOS (FEW-SHOT — CONSTRUCCIÓN DE APPS)]:\n"
        "Ejemplo 1:\n"
        "Usuario: Crea una app de notas en HTML/JS.\n"
        "Asistente: -> define un plan breve de 2-3 líneas -> escribe los archivos completos en bloques ```file:index.html y ```file:app.js "
        "sin recortar funciones ni dejar TODOs -> resume brevemente cómo abrirla.\n"
    )


def get_bedrock_converse_tools() -> List[Dict[str, Any]]:
    """Convierte el catálogo MCP al esquema nativo toolConfig de Amazon Bedrock (Nova / Claude)."""
    return [
        {
            "toolSpec": {
                "name": t["name"],
                "description": t["description"],
                "inputSchema": {"json": t["inputSchema"]}
            }
        }
        for t in MCP_TOOLS_CATALOG
    ]


# ==============================================================================
# 2. SERVIDOR Y EJECUTOR MCP (MODEL CONTEXT PROTOCOL JSON-RPC 2.0)
# ==============================================================================

class MCPServerEngine:
    """
    Servidor oficial Model Context Protocol (MCP) integrado:
    Gestiona herramientas (Tools), recursos del repositorio (Resources) y plantillas (Prompts),
    y ejecuta invocaciones de forma segura dentro del workspace activo.
    """

    def __init__(self):
        self.protocol_version = "2024-11-05"
        self.server_info = {
            "name": "Antigravity-SOTA-MCP-Server",
            "version": "2.5.0"
        }

    def execute_tool(self, tool_name: str, arguments: Dict[str, Any], workspace_dir: Path) -> Dict[str, Any]:
        if mcp_client_manager.is_external_tool(tool_name):
            return mcp_client_manager.call_external_tool(tool_name, arguments or {})
        t0 = time.time()
        ws = workspace_dir.resolve()
        try:
            if tool_name == "read_file":
                rel_p = str(arguments.get("path", "")).strip().lstrip("/\\")
                target = (ws / rel_p).resolve()
                if not str(target).startswith(str(ws)) or not target.exists() or not target.is_file():
                    return {"success": False, "error": f"Archivo no encontrado o fuera del workspace: {rel_p}"}
                lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
                s_l = max(1, int(arguments.get("start_line", 1)))
                e_l = min(len(lines), int(arguments.get("end_line", s_l + 220)))
                numbered = "\n".join(f"{i:04d} | {lines[i-1]}" for i in range(s_l, e_l + 1))
                res = {"success": True, "path": rel_p, "total_lines": len(lines), "range": f"{s_l}-{e_l}", "content": numbered}

            elif tool_name == "write_file":
                rel_p = str(arguments.get("path", "")).strip().lstrip("/\\")
                content = str(arguments.get("content", ""))
                target = (ws / rel_p).resolve()
                if not str(target).startswith(str(ws)):
                    return {"success": False, "error": "Ruta fuera del workspace permitido."}
                if rel_p.lower().endswith(".py"):
                    content = auto_fix_common_python_issues(content, rel_p, workspace_dir=ws)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                ast_valid = True
                ast_msg = "Guardado"
                if rel_p.lower().endswith(".py"):
                    try:
                        ast.parse(content, filename=rel_p)
                        ast_msg = "Sintaxis AST verificada"
                    except SyntaxError as se:
                        ast_valid = False
                        ast_msg = f"SyntaxError línea {se.lineno}: {se.msg}"
                codebase_rag_store.index_workspace(ws, force=False)
                res = {"success": True, "path": rel_p, "bytes_written": len(content), "ast_valid": ast_valid, "ast_status": ast_msg}

            elif tool_name == "apply_patch":
                rel_p = str(arguments.get("path", "")).strip().lstrip("/\\")
                search_b = str(arguments.get("search_block", ""))
                replace_b = str(arguments.get("replace_block", ""))
                target = (ws / rel_p).resolve()
                if not str(target).startswith(str(ws)) or not target.exists():
                    return {"success": False, "error": f"Archivo no existe para parchear: {rel_p}"}
                orig = target.read_text(encoding="utf-8", errors="replace")
                if search_b in orig:
                    updated = orig.replace(search_b, replace_b, 1)
                    target.write_text(updated, encoding="utf-8")
                    codebase_rag_store.index_workspace(ws, force=False)
                    res = {"success": True, "path": rel_p, "patched": True}
                else:
                    res = {"success": False, "path": rel_p, "error": "El bloque search_block no coincidió exactamente."}

            elif tool_name == "list_workspace_files":
                files_list = []
                for root, dirs, files in os.walk(ws):
                    dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules", ".venv", ".antigravity_rag", "models", "engine")]
                    for f in files:
                        fp = Path(root) / f
                        rel = str(fp.relative_to(ws)).replace("\\", "/")
                        files_list.append({"path": rel, "size_bytes": fp.stat().st_size})
                res = {"success": True, "workspace": str(ws), "files": files_list[:150]}

            elif tool_name == "search_codebase":
                q = str(arguments.get("query", ""))
                top_k = int(arguments.get("top_k", 5))
                hits = codebase_rag_store.search(ws, q, top_k=top_k)
                res = {"success": True, "query": q, "results": hits}

            elif tool_name == "query_cyber_graph":
                q = str(arguments.get("query", ""))
                if arguments.get("sync_nvd"):
                    dynamic_cyber_graph.sync_nvd_cves(q, max_results=2)
                nodes = dynamic_cyber_graph.query_subgraph(q, max_nodes=3)
                res = {"success": True, "query": q, "nodes": nodes}

            elif tool_name == "run_sandbox_command":
                cmd = str(arguments.get("command", ""))
                t_out = min(60, max(5, int(arguments.get("timeout_s", 20))))
                res = terminal_engine.run_command_sync(cmd, cwd=ws, timeout_s=t_out)

            elif tool_name == "audit_dependencies":
                rel_p = str(arguments.get("file_path", "main.py")).strip()
                target = (ws / rel_p).resolve()
                all_d = terminal_engine.find_dependencies(target)
                miss_d = terminal_engine.check_missing_dependencies(target)
                installed_now = []
                if miss_d and arguments.get("auto_install", True):
                    ok_i, _ = terminal_engine.install_dependencies(miss_d)
                    if ok_i:
                        installed_now = miss_d
                        miss_d = terminal_engine.check_missing_dependencies(target)
                res = {"success": True, "file": rel_p, "dependencies": all_d, "missing": miss_d, "installed_now": installed_now}

            elif tool_name == "inspect_webpage":
                target_str = str(arguments.get("target", "")).strip()
                if not target_str:
                    return {"success": False, "error": "Falta especificar 'target' (URL o ruta .html)."}
                status_code = 200
                transport = "local_file"
                if target_str.startswith("http://") or target_str.startswith("https://"):
                    status_code, raw_bytes, transport = _fetch_url_bytes(target_str, timeout=25.0)
                    html_text = raw_bytes.decode("utf-8", errors="replace")
                else:
                    rel_p = target_str.lstrip("/\\")
                    local_f = (ws / rel_p).resolve()
                    if not str(local_f).startswith(str(ws)) or not local_f.exists():
                        return {"success": False, "error": f"Archivo HTML local no encontrado: {target_str}"}
                    html_text = local_f.read_text(encoding="utf-8", errors="replace")

                analyzer = _StructuredHTMLAnalyzer()
                analyzer.feed(html_text)
                res = {
                    "success": True,
                    "target": target_str,
                    "status_code": status_code,
                    "transport": transport,
                    "title": analyzer.title,
                    "metas": analyzer.metas[:12],
                    "headings": analyzer.headings[:25],
                    "forms": analyzer.forms[:10],
                    "scripts": analyzer.scripts[:20],
                    "stylesheets": analyzer.stylesheets[:15],
                    "links": analyzer.links[:30],
                    "text_preview": " ".join(analyzer.text_chunks)[:2200],
                    "html_bytes": len(html_text.encode("utf-8", errors="ignore"))
                }

            elif tool_name == "download_url":
                url = _normalize_raw_url(str(arguments.get("url", "")))
                if not (url.startswith("http://") or url.startswith("https://")):
                    return {"success": False, "error": "La URL debe comenzar con http:// o https://"}

                fname = str(arguments.get("filename") or "").strip().lstrip("/\\")
                if not fname:
                    parsed = urllib.parse.urlparse(url)
                    raw_name = Path(parsed.path).name
                    fname = urllib.parse.unquote(raw_name) if raw_name else "archivo_descargado.txt"
                    if "." not in fname:
                        fname = "archivo_descargado.txt"
                target = (ws / fname).resolve()
                if not str(target).startswith(str(ws)):
                    return {"success": False, "error": "Ruta fuera del proyecto abierto."}

                actual_url = url
                fallback_used = False
                dl_info = None
                try:
                    dl_info = _stream_download_to_file(url, target, expected_ext=target.suffix.lower())
                except Exception as first_err:
                    # Si era un repo de GitHub con rama master en lugar de main
                    if url.endswith("/archive/refs/heads/main.zip"):
                        master_url = url.replace("/archive/refs/heads/main.zip", "/archive/refs/heads/master.zip")
                        try:
                            dl_info = _stream_download_to_file(master_url, target, expected_ext=".zip")
                            actual_url = master_url
                        except Exception:
                            dl_info = None

                    # Recuperación automática si la URL dio 404/HTML: buscar una URL real verificada del mismo tipo
                    if dl_info is None:
                        ext_req = target.suffix.lower() if target.name != "archivo_descargado.txt" else ""
                        candidates = _find_verified_download_urls(f"{target.stem} {ext_req}", target_ext=ext_req, max_results=4) if ext_req else []
                        for cand in candidates:
                            try:
                                cand_target = target
                                if cand.get("filename") and not arguments.get("filename"):
                                    fname = cand["filename"]
                                    cand_target = (ws / fname).resolve()
                                dl_info = _stream_download_to_file(cand["url"], cand_target, expected_ext=ext_req)
                                actual_url = cand["url"]
                                target = dl_info["final_path"]
                                fallback_used = True
                                break
                            except Exception:
                                continue
                        if dl_info is None:
                            return {
                                "success": False,
                                "error": f"No se pudo descargar {url} ({first_err})",
                                "suggested_verified_urls": [c["url"] for c in candidates[:3]]
                            }

                final_path = dl_info["final_path"]
                final_rel = str(final_path.relative_to(ws)).replace("\\", "/")
                codebase_rag_store.index_workspace(ws, force=False)
                res = {
                    "success": True,
                    "filename": final_rel,
                    "path": str(final_path),
                    "size_bytes": dl_info["size_bytes"],
                    "content_type": dl_info.get("content_type", ""),
                    "source_url": actual_url,
                    "fallback_recovered": fallback_used,
                    "transport": dl_info.get("transport", "direct"),
                    "refresh_workspace": True
                }

            elif tool_name == "search_web":
                query = str(arguments.get("query", "")).strip()
                if not query:
                    return {"success": False, "error": "Falta el parámetro 'query'."}
                found = _find_verified_download_urls(query, max_results=8)
                res = {
                    "success": True,
                    "query": query,
                    "results": found
                }

            elif tool_name == "compile_project":
                rel_p = str(arguments.get("file_path") or "").strip().lstrip("/\\")
                targets: List[Path] = []
                if rel_p:
                    tf = (ws / rel_p).resolve()
                    if not str(tf).startswith(str(ws)) or not tf.exists():
                        return {"success": False, "error": f"Archivo no encontrado: {rel_p}"}
                    targets = [tf]
                else:
                    for root, dirs, files in os.walk(ws):
                        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules", ".venv", ".antigravity_rag", "models", "engine")]
                        for f in files:
                            if f.endswith((".py", ".json")):
                                targets.append(Path(root) / f)
                errors = []
                checked = 0
                for tf in targets[:100]:
                    rel_item = str(tf.relative_to(ws)).replace("\\", "/")
                    checked += 1
                    if tf.suffix.lower() == ".py":
                        try:
                            py_compile.compile(str(tf), doraise=True)
                        except py_compile.PyCompileError as pe:
                            errors.append({"file": rel_item, "error": str(pe)})
                    elif tf.suffix.lower() == ".json":
                        try:
                            json.loads(tf.read_text(encoding="utf-8", errors="replace"))
                        except Exception as je:
                            errors.append({"file": rel_item, "error": f"JSON inválido: {je}"})
                res = {
                    "success": len(errors) == 0,
                    "files_checked": checked,
                    "errors": errors
                }

            elif tool_name == "grep_search":
                pat = str(arguments.get("pattern", "")).strip()
                max_m = min(60, max(5, int(arguments.get("max_matches", 25))))
                if not pat:
                    return {"success": False, "error": "Falta especificar 'pattern'."}
                try:
                    rx = re.compile(pat, re.IGNORECASE)
                except re.error:
                    rx = re.compile(re.escape(pat), re.IGNORECASE)
                matches = []
                ignored = {".git", "__pycache__", "node_modules", ".venv", "venv", ".antigravity_rag", "models", "engine"}
                for root, dirs, files in os.walk(ws):
                    dirs[:] = [d for d in dirs if d not in ignored and not d.startswith(".")]
                    for fname in files:
                        fp = Path(root) / fname
                        if fp.stat().st_size > 400_000:
                            continue
                        try:
                            text_lines = fp.read_text(encoding="utf-8", errors="ignore").splitlines()
                        except Exception:
                            continue
                        rel_f = str(fp.relative_to(ws)).replace("\\", "/")
                        for idx_l, ln in enumerate(text_lines, 1):
                            if rx.search(ln):
                                matches.append({"file": rel_f, "line": idx_l, "text": ln.strip()[:160]})
                                if len(matches) >= max_m:
                                    break
                        if len(matches) >= max_m:
                            break
                    if len(matches) >= max_m:
                        break
                res = {"success": True, "pattern": pat, "total_matches": len(matches), "matches": matches}

            elif tool_name == "inspect_code_graph":
                g_info = code_arch_graph.analyze_workspace_graph(ws)
                res = {
                    "success": True,
                    "nodes_count": len(g_info.get("nodes", [])),
                    "edges_count": len(g_info.get("edges", [])),
                    "broken_edges": g_info.get("broken_edges", []),
                    "graph_summary": g_info.get("prompt_context", "")[:3200]
                }

            elif tool_name == "delete_file":
                rel_p = str(arguments.get("path", "")).strip().lstrip("/\\")
                target = (ws / rel_p).resolve()
                if not str(target).startswith(str(ws)) or not target.exists() or not target.is_file():
                    return {"success": False, "error": f"Archivo no encontrado o fuera del workspace: {rel_p}"}
                target.unlink()
                res = {"success": True, "deleted": rel_p}

            elif tool_name == "web_search":
                q = str(arguments.get("query", "")).strip()
                max_r = min(6, max(1, int(arguments.get("max_results", 4))))
                if not q:
                    return {"success": False, "error": "Falta especificar 'query'."}
                proxy_url = _get_active_proxy_url("https://duckduckgo.com")
                results_out = []
                try:
                    try:
                        from ddgs import DDGS as _DDGS
                    except Exception:
                        from duckduckgo_search import DDGS as _DDGS
                    with _DDGS(proxy=proxy_url, timeout=12.0 if proxy_url else 7.0) as ddgs:
                        for item in ddgs.text(q, max_results=max_r):
                            results_out.append({
                                "title": item.get("title", ""),
                                "url": item.get("href", ""),
                                "snippet": item.get("body", "")[:300]
                            })
                except Exception as we:
                    return {"success": False, "error": f"Error en búsqueda web: {we}"}
                res = {"success": True, "query": q, "via_proxy": bool(proxy_url), "results": results_out}

            else:
                res = {"success": False, "error": f"Herramienta MCP desconocida: {tool_name}"}

        except Exception as e:
            res = {"success": False, "error": str(e)}

        res["elapsed_ms"] = round((time.time() - t0) * 1000, 2)
        audit_logger("mcp_tool_call", {"tool": tool_name, "args": arguments, "success": res.get("success", False)})
        return res

    def handle_jsonrpc(self, payload: Dict[str, Any], workspace_dir: Path) -> Dict[str, Any]:
        """Manejador estándar JSON-RPC 2.0 del protocolo MCP."""
        req_id = payload.get("id", 1)
        method = payload.get("method", "")
        params = payload.get("params", {}) or {}

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": self.protocol_version,
                    "serverInfo": self.server_info,
                    "capabilities": {"tools": {}, "resources": {}, "prompts": {}}
                }
            }
        elif method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS_CATALOG}}
        elif method == "tools/call":
            t_name = params.get("name", "")
            t_args = params.get("arguments", {}) or {}
            out = self.execute_tool(t_name, t_args, workspace_dir)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=2)}],
                    "isError": not out.get("success", False)
                }
            }
        elif method == "resources/list":
            files_res = self.execute_tool("list_workspace_files", {}, workspace_dir)
            resources = [
                {"uri": f"file:///{f['path']}", "name": f["path"], "mimeType": "text/plain"}
                for f in files_res.get("files", [])
            ]
            return {"jsonrpc": "2.0", "id": req_id, "result": {"resources": resources}}
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Método MCP no soportado: {method}"}
            }


# ==============================================================================
# 3. PARSER UNIVERSAL DE TOOL CALLS Y BUCLE AGÉNTICO REACT (REASON + ACT)
# ==============================================================================

def parse_tool_calls_from_text(text: str) -> List[Dict[str, Any]]:
    """
    Detecta invocaciones nativas de herramientas emitidas por Qwen (<tool_call>...</tool_call>)
    o bloques JSON estructurados {"tool": "...", "arguments": {...}}.
    """
    calls = []
    if not text:
        return calls

    # 1. Formato nativo Qwen2.5 / Qwen3: <tool_call>{"name": "...", "arguments": {...}}</tool_call>
    tc_matches = re.findall(r"<tool_call>\s*(\{[\s\S]*?\})\s*</tool_call>", text, re.IGNORECASE)
    for raw_json in tc_matches:
        try:
            obj = json.loads(raw_json)
            name = obj.get("name") or obj.get("tool")
            args = obj.get("arguments") or obj.get("parameters") or {}
            if isinstance(args, str):
                args = json.loads(args)
            if name:
                calls.append({"name": name, "arguments": args})
        except Exception:
            pass

    if calls:
        return calls

    # 2. Formato bloque ```tool_call o ```json con clave "tool_call" / "name"
    block_matches = re.findall(r"```(?:tool_call|mcp_call)\s*[\r\n]+(\{[\s\S]*?\})\s*```", text, re.IGNORECASE)
    for raw_json in block_matches:
        try:
            obj = json.loads(raw_json)
            name = obj.get("name") or obj.get("tool")
            args = obj.get("arguments") or obj.get("parameters") or {}
            if name:
                calls.append({"name": name, "arguments": args})
        except Exception:
            pass

    return calls


def run_autonomous_react_pre_step(
    user_query: str,
    workspace_dir: Path,
    active_file: Optional[str] = None,
    local_port: int = 8085
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Bucle Agéntico ReAct / Plan-and-Execute:
    Evalúa de forma autónoma qué herramientas MCP necesita invocar antes o durante la respuesta
    (búsqueda semántica Codebase RAG, consulta al Grafo Dinámico STIX/CVE, lectura de archivos o
    auditoría de dependencias) y devuelve la traza de herramientas ejecutadas junto con las observaciones.
    """
    executed_tools: List[Dict[str, Any]] = []
    observations: List[str] = []
    q_low = user_query.lower()

    # 1. Si el repositorio tiene archivos de código, activar búsqueda semántica Codebase RAG automáticamente
    rag_hits = codebase_rag_store.search(workspace_dir, user_query, top_k=4)
    if rag_hits:
        executed_tools.append({
            "tool": "search_codebase",
            "arguments": {"query": user_query[:80], "top_k": len(rag_hits)},
            "summary": f"Codebase RAG recuperó {len(rag_hits)} fragmentos AST ({', '.join(h['symbol_name'] for h in rag_hits[:3])})"
        })
        rag_ctx = codebase_rag_store.build_rag_prompt_context(workspace_dir, user_query, active_file=active_file, top_k=4)
        if rag_ctx:
            observations.append(rag_ctx)

    # 2. Si el usuario menciona un CVE o pregunta por vulnerabilidades específicas, invocar query_cyber_graph
    cve_matches = re.findall(r"\b(CVE-\d{4}-\d{4,7})\b", user_query, re.IGNORECASE)
    if cve_matches:
        for cve_id in cve_matches[:2]:
            res_kg = mcp_server.execute_tool("query_cyber_graph", {"query": cve_id, "sync_nvd": True}, workspace_dir)
            executed_tools.append({
                "tool": "query_cyber_graph",
                "arguments": {"query": cve_id, "sync_nvd": True},
                "summary": f"Sincronizado {cve_id} con Grafo Dinámico STIX/NVD"
            })

    # 3. Si el usuario pide inspeccionar o auditar dependencias de un archivo específico
    if any(w in q_low for w in ("dependencia", "libreria", "librería", "pip", "imports", "paquetes")):
        target_f = active_file or "main.py"
        dep_res = mcp_server.execute_tool("audit_dependencies", {"file_path": target_f, "auto_install": False}, workspace_dir)
        executed_tools.append({
            "tool": "audit_dependencies",
            "arguments": {"file_path": target_f},
            "summary": f"Auditoría AST de {target_f}: {len(dep_res.get('dependencies', []))} paquetes detectados"
        })
        observations.append(f"\n[Observación MCP audit_dependencies]: {json.dumps(dep_res, ensure_ascii=False)}\n")

    # 4. Detección automática de URLs o intención de descarga desde internet
    url_matches = re.findall(r"(https?://[^\s<>\"'`]+)", user_query, re.IGNORECASE)
    wants_download = any(w in q_low for w in ("descarga", "descargar", "baja", "bajar", "download"))
    if url_matches:
        for target_url in url_matches[:2]:
            if wants_download:
                dl_res = mcp_server.execute_tool("download_url", {"url": target_url}, workspace_dir)
                executed_tools.append({
                    "tool": "download_url",
                    "arguments": {"url": target_url},
                    "summary": f"Descarga directa a proyecto: {dl_res.get('filename', target_url)} ({'OK' if dl_res.get('success') else 'Error'})"
                })
                observations.append(f"\n[Observación MCP download_url]: {json.dumps(dl_res, ensure_ascii=False)}\n")
            else:
                web_res = mcp_server.execute_tool("inspect_webpage", {"target": target_url}, workspace_dir)
                executed_tools.append({
                    "tool": "inspect_webpage",
                    "arguments": {"target": target_url},
                    "summary": f"Estructura web analizada ({web_res.get('transport', 'web')}): {target_url}"
                })
                observations.append(f"\n[Observación MCP inspect_webpage]: {json.dumps(web_res, ensure_ascii=False)[:3500]}\n")
    elif wants_download:
        req_ext = _detect_requested_extension(user_query)
        candidates = _find_verified_download_urls(user_query, target_ext=req_ext, max_results=5)
        downloaded_ok = None
        for cand in candidates[:3]:
            cand_url = cand.get("url", "")
            cand_name = cand.get("filename", "")
            if not cand_url:
                continue
            dl_try = mcp_server.execute_tool("download_url", {"url": cand_url, "filename": cand_name}, workspace_dir)
            if dl_try.get("success"):
                downloaded_ok = dl_try
                break

        if downloaded_ok:
            executed_tools.append({
                "tool": "download_url",
                "arguments": {"url": downloaded_ok.get("source_url", ""), "filename": downloaded_ok.get("filename", "")},
                "summary": f"Descargado al repo: {downloaded_ok.get('filename')} ({downloaded_ok.get('size_bytes', 0)} bytes)"
            })
            observations.append(
                f"\n[Observación MCP download_url — ARCHIVO YA DESCARGADO CON ÉXITO EN EL REPOSITORIO]:\n"
                f"{json.dumps(downloaded_ok, ensure_ascii=False)}\n"
                "INSTRUCCIÓN: El archivo YA fue descargado y guardado en la carpeta del proyecto. "
                "Confírmalo claramente al usuario indicando el nombre exacto del archivo, su tamaño y la URL fuente real. "
                "NO vuelvas a llamar a download_url ni inventes código innecesario salvo que el usuario haya pedido integrarlo en una app.\n"
            )
        else:
            executed_tools.append({
                "tool": "search_web",
                "arguments": {"query": user_query},
                "summary": f"Búsqueda de archivo descargable: {len(candidates)} candidatos"
            })
            observations.append(f"\n[Observación MCP search_web]: {json.dumps({'results': candidates}, ensure_ascii=False)[:3500]}\n")
    elif active_file and active_file.lower().endswith((".html", ".htm")) and any(w in q_low for w in ("analiza", "estructura", "pagina", "página", "web", "dom", "revisa")):
        web_res = mcp_server.execute_tool("inspect_webpage", {"target": active_file}, workspace_dir)
        executed_tools.append({
            "tool": "inspect_webpage",
            "arguments": {"target": active_file},
            "summary": f"Estructura HTML local analizada: {active_file}"
        })
        observations.append(f"\n[Observación MCP inspect_webpage]: {json.dumps(web_res, ensure_ascii=False)[:3500]}\n")

    # 5. Compilación y validación de errores si el usuario pide compilar o revisar sintaxis
    if any(w in q_low for w in ("compila", "compilar", "sintaxis", "syntax", "verifica errores", "valida el proyecto")):
        comp_res = mcp_server.execute_tool("compile_project", {"file_path": active_file or ""}, workspace_dir)
        executed_tools.append({
            "tool": "compile_project",
            "arguments": {"file_path": active_file or ""},
            "summary": f"Compilación: {comp_res.get('files_checked', 0)} archivos, {len(comp_res.get('errors', []))} errores"
        })
        observations.append(f"\n[Observación MCP compile_project]: {json.dumps(comp_res, ensure_ascii=False)}\n")

    return executed_tools, "\n".join(observations)


mcp_server = MCPServerEngine()
