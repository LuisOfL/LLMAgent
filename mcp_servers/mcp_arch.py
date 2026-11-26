# -*- coding: utf-8 -*-
"""
mcp_servers/mcp_arch.py — Servidor MCP #1 (MCP-ARCH): Plano DAG y Contratos Arquitectónicos.
"""

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
        "name": "build_dag_plan",
        "description": "Valida y ordena topológicamente un plano DAG de archivos según arquetipo y dependencias.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "archetype": {"type": "string", "description": "single_html | static_web | fullstack_web | python_app"},
                "files": {"type": "array", "items": {"type": "object"}}
            },
            "required": ["files"]
        },
    },
    {
        "name": "extract_contracts",
        "description": "Extrae contratos reales (AST Python, IDs DOM HTML, variables CSS, funciones/endpoints JS) de archivos del workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_dir": {"type": "string"},
                "file_paths": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["workspace_dir", "file_paths"]
        },
    },
]


def _build_dag_plan(args: Dict[str, Any]) -> Dict[str, Any]:
    archetype = str(args.get("archetype") or "python_app").lower()
    raw_files: List[Dict[str, Any]] = list(args.get("files") or [])

    layer_prio = {"models": 0, "core": 1, "service": 2, "api": 3, "entrypoint": 4, "ui": 5}

    def _sort_key(item: Dict[str, Any]):
        p = str(item.get("path", "")).lower()
        if archetype == "static_web":
            if p.endswith((".html", ".htm")): return (0, p)
            if p.endswith(".css"): return (1, p)
            if p.endswith(".js"): return (2, p)
            return (3, p)
        if archetype == "fullstack_web":
            if p.endswith(".py"): return (layer_prio.get(str(item.get("layer", "core")).lower(), 2), p)
            if p.endswith((".html", ".htm")): return (10, p)
            if p.endswith(".css"): return (11, p)
            if p.endswith(".js"): return (12, p)
            return (13, p)
        return (layer_prio.get(str(item.get("layer", "core")).lower(), 2), p)

    ordered = sorted(raw_files, key=_sort_key)
    dag_edges = []
    for f in ordered:
        for dep in (f.get("imports_from") or []):
            dag_edges.append({"from": str(dep), "to": str(f.get("path", ""))})

    return {
        "success": True,
        "archetype": archetype,
        "ordered_files": ordered,
        "dag_edges": dag_edges,
    }


def _extract_contracts(args: Dict[str, Any]) -> Dict[str, Any]:
    ws = Path(str(args.get("workspace_dir", "."))).resolve()
    file_paths = args.get("file_paths") or []
    contracts = []
    for rel in file_paths:
        rel_clean = str(rel).replace("\\", "/").lstrip("/")
        fp = (ws / rel_clean).resolve()
        if not str(fp).startswith(str(ws)) or not fp.exists() or not fp.is_file():
            continue
        txt = fp.read_text(encoding="utf-8", errors="replace")
        sig = extract_module_signatures(rel_clean, txt)
        if sig:
            contracts.append(sig)
    return {
        "success": True,
        "contracts_text": "\n".join(contracts),
        "count": len(contracts),
    }


if __name__ == "__main__":
    run_stdio_mcp_server(
        "mcp-arch",
        "1.0.0",
        TOOLS,
        {"build_dag_plan": _build_dag_plan, "extract_contracts": _extract_contracts},
    )
