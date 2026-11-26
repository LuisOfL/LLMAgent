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


def _infer_python_priority(path_low: str, declared_layer: str) -> int:
    """Infiere la prioridad topológica real (bottom-up) para que models/engines vayan SIEMPRE antes que main.py/server.py."""
    name = Path(path_low).name
    if name in ("models.py", "schemas.py", "schema.py", "config.py", "settings.py", "constants.py", "database.py", "db.py"):
        return 0
    if any(k in name for k in ("util", "helper", "common", "types")):
        return 1
    if any(k in name for k in ("engine", "scanner", "core", "interceptor", "spoof", "worker", "manager")):
        return 2
    if any(k in name for k in ("service", "api", "router", "routes", "controller")):
        return 3
    if name in ("server.py", "app.py", "main.py", "cli.py", "run.py", "wsgi.py", "asgi.py"):
        return 5
    layer_prio = {"models": 0, "core": 2, "service": 3, "api": 4, "entrypoint": 5, "ui": 6}
    return layer_prio.get(declared_layer.lower(), 2)


def _topological_refine(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Refina el orden respetando `imports_from` explícitos (Kahn estable sin ciclos)."""
    by_path = {str(f.get("path", "")).replace("\\", "/").lstrip("/"): f for f in items}
    order = [str(f.get("path", "")).replace("\\", "/").lstrip("/") for f in items]
    indeg = {p: 0 for p in order}
    adj: Dict[str, List[str]] = {p: [] for p in order}

    for f in items:
        dst = str(f.get("path", "")).replace("\\", "/").lstrip("/")
        for dep in (f.get("imports_from") or []):
            dep_clean = str(dep).replace("\\", "/").lstrip("/")
            if dep_clean in by_path and dep_clean != dst and dst not in adj[dep_clean]:
                adj[dep_clean].append(dst)
                indeg[dst] += 1

    ready = [p for p in order if indeg[p] == 0]
    result: List[str] = []
    while ready:
        cur = ready.pop(0)
        result.append(cur)
        for nxt in adj[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
    # Si hubo un ciclo, preservar los nodos restantes en el orden base
    for p in order:
        if p not in result:
            result.append(p)
    return [by_path[p] for p in result]


def _build_dag_plan(args: Dict[str, Any]) -> Dict[str, Any]:
    archetype = str(args.get("archetype") or "python_app").lower()
    raw_files: List[Dict[str, Any]] = list(args.get("files") or [])

    def _sort_key(item: Dict[str, Any]):
        p = str(item.get("path", "")).lower().replace("\\", "/")
        declared = str(item.get("layer", "core"))
        if archetype == "static_web":
            if p.endswith((".html", ".htm")): return (0, p)
            if p.endswith(".css"): return (1, p)
            if p.endswith(".js"): return (2, p)
            return (3, p)
        if archetype == "fullstack_web":
            if p.endswith(".py"): return (_infer_python_priority(p, declared), p)
            if p.endswith((".html", ".htm")): return (10, p)
            if p.endswith(".css"): return (11, p)
            if p.endswith(".js"): return (12, p)
            return (13, p)
        if p.endswith(".py"):
            return (_infer_python_priority(p, declared), p)
        return (6, p)

    base_ordered = sorted(raw_files, key=_sort_key)
    ordered = _topological_refine(base_ordered)
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
