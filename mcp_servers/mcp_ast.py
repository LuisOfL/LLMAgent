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


def _write_module(args: Dict[str, Any]) -> Dict[str, Any]:
    ws = Path(str(args.get("workspace_dir", "."))).resolve()
    rel = str(args.get("file_path", ""))
    content = str(args.get("content", ""))
    target = _safe_target(ws, rel)
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
