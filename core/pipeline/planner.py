# -*- coding: utf-8 -*-
"""
core/pipeline/planner.py — Planificador desacoplado (FASE 1: PLAN DAG + FASE 2: ASSETS).
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from core.mcp_client import mcp_client_manager
from core.local_codex_engine import detect_app_archetype, plan_blueprint_constrained


def call_mcp_json(namespaced_tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Invoca una herramienta de servidor MCP externo (JSON-RPC 2.0 stdio) y parsea su JSON."""
    res = mcp_client_manager.call_external_tool(namespaced_tool, arguments)
    if not res.get("success") and "no está conectado" in str(res.get("error", "")):
        mcp_client_manager.reload_servers()
        res = mcp_client_manager.call_external_tool(namespaced_tool, arguments)
    out = res.get("output")
    if isinstance(out, str):
        try:
            return json.loads(out)
        except Exception:
            return {"success": bool(res.get("success")), "raw": out}
    return res if isinstance(res, dict) else {"success": False, "error": "Respuesta MCP vacía"}


class AppPlanner:
    """Responsable exclusivamente de diseñar el DAG topológico y resolver assets previos."""

    def __init__(self, local_port: int = 8085):
        self.local_port = local_port

    def plan_dag(self, user_query: str, workspace_dir: Path) -> Tuple[str, str, List[Dict[str, str]]]:
        archetype = detect_app_archetype(user_query, workspace_dir)
        plan_summary, plan_files = plan_blueprint_constrained(
            user_query,
            local_port=self.local_port,
            workspace_dir=workspace_dir,
        )
        dag_res = call_mcp_json("mcp__mcp_arch__build_dag_plan", {"archetype": archetype, "files": plan_files})
        if dag_res.get("success") and dag_res.get("ordered_files"):
            plan_files = dag_res["ordered_files"]
        return archetype, plan_summary, plan_files

    def fetch_assets(self, user_query: str, workspace_dir: Path) -> Tuple[List[str], str]:
        ws_str = str(workspace_dir.resolve())
        asset_res = call_mcp_json(
            "mcp__mcp_assets__fetch_project_assets",
            {"workspace_dir": ws_str, "query": user_query, "auto_download": True},
        )
        sec_res = call_mcp_json("mcp__mcp_assets__query_sec_kb", {"query": user_query})

        extra_context_parts: List[str] = []
        if asset_res.get("downloaded"):
            dl = asset_res["downloaded"]
            extra_context_parts.append(
                f"ASSET REAL VERIFICADO EN DISCO (USA ESTA RUTA EXACTA): ./{dl['filename']} ({dl['size_bytes']} bytes)"
            )
            detail = f"Asset descargado: {dl['filename']} ({round(dl['size_bytes'] / 1024 / 1024, 2)} MB)"
        elif asset_res.get("skipped"):
            detail = "Sin descarga externa requerida"
        else:
            detail = f"Aviso descarga: {asset_res.get('error', 'sin candidatos')}"

        if sec_res.get("active") and sec_res.get("context"):
            extra_context_parts.append(sec_res["context"])
        return extra_context_parts, detail
