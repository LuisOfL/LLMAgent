# -*- coding: utf-8 -*-
"""
mcp_servers/mcp_assets.py — Servidor MCP #3 (MCP-ASSETS): Assets Públicos (3D/UI/Data) + Base de Conocimiento Técnico/CVE.
"""

import sys
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from mcp_servers._rpc_base import run_stdio_mcp_server
from core.mcp_agent_engine import _detect_requested_extension, _find_verified_download_urls, _stream_download_to_file
from core.cybersecurity_kg import get_cybersecurity_graph_context

TOOLS = [
    {
        "name": "fetch_project_assets",
        "description": "Busca y descarga assets públicos verificados (modelos .glb, imágenes, datasets, PDFs) directamente al workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_dir": {"type": "string"},
                "query": {"type": "string"},
                "auto_download": {"type": "boolean", "default": True}
            },
            "required": ["workspace_dir", "query"]
        },
    },
    {
        "name": "query_sec_kb",
        "description": "Consulta la base de conocimiento técnica / CVE / MITRE local para enriquecer herramientas de red o seguridad.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"}
            },
            "required": ["query"]
        },
    },
]


def _fetch_project_assets(args: Dict[str, Any]) -> Dict[str, Any]:
    ws = Path(str(args.get("workspace_dir", "."))).resolve()
    query = str(args.get("query", "")).strip()
    auto_dl = bool(args.get("auto_download", True))
    ext = _detect_requested_extension(query)
    if not ext:
        return {"success": True, "skipped": True, "reason": "No se detectó solicitud de asset descargable."}

    candidates = _find_verified_download_urls(query, target_ext=ext, max_results=4)
    if not candidates and ext in (".glb", ".gltf"):
        candidates = [
            {
                "title": "Khronos DamagedHelmet.glb",
                "filename": "DamagedHelmet.glb",
                "url": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb",
            },
            {
                "title": "Khronos ToyCar.glb",
                "filename": "ToyCar.glb",
                "url": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/ToyCar/glTF-Binary/ToyCar.glb",
            },
        ]
    if not auto_dl or not candidates:
        return {"success": True, "candidates": candidates, "downloaded": None}

    for cand in candidates[:3]:
        fname = cand.get("filename") or f"asset{ext}"
        target = (ws / fname).resolve()
        if not str(target).startswith(str(ws)):
            continue
        try:
            info = _stream_download_to_file(cand["url"], target, expected_ext=ext)
            final_rel = str(info["final_path"].relative_to(ws)).replace("\\", "/")
            return {
                "success": True,
                "downloaded": {
                    "filename": final_rel,
                    "size_bytes": info["size_bytes"],
                    "source_url": cand["url"],
                },
                "refresh_workspace": True,
            }
        except Exception:
            continue

    return {"success": False, "error": "No se pudo descargar ningún candidato verificado.", "candidates": candidates[:2]}


def _query_sec_kb(args: Dict[str, Any]) -> Dict[str, Any]:
    query = str(args.get("query", "")).strip()
    kg = get_cybersecurity_graph_context(query)
    return {
        "success": True,
        "active": bool(kg and kg.get("is_active")),
        "entities": kg.get("entities", []) if kg else [],
        "context": (kg.get("prompt_text", "")[:2200]) if kg else "",
    }


if __name__ == "__main__":
    run_stdio_mcp_server(
        "mcp-assets",
        "1.0.0",
        TOOLS,
        {"fetch_project_assets": _fetch_project_assets, "query_sec_kb": _query_sec_kb},
    )
