# -*- coding: utf-8 -*-
"""
core/pipeline/verifier.py — Verificador Determinista (MCP-EXEC) + Runtime Vision Opcional bajo flag.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from core.pipeline.planner import call_mcp_json


class DeterministicVerifier:
    """
    Ejecuta QA determinista por defecto (AST, Ruff, Node --check, wiring DOM/API/eventos, Three.js render loop, sandbox).
    `MCP-RUNTIME-VISION` (Playwright) se mantiene como fase opcional bajo flag (`enable_runtime_vision=False` por defecto)
    para no robar RAM/CPU al LLM en el camino crítico.
    """

    def __init__(self, enable_runtime_vision: bool = False):
        env_flag = os.environ.get("LLMAGENT_ENABLE_RUNTIME_VISION", "").strip().lower() in ("1", "true", "yes")
        self.enable_runtime_vision = bool(enable_runtime_vision or env_flag)

    def verify_static(self, workspace_dir: Path, built_paths: List[str]) -> Tuple[List[Tuple[str, str]], List[str]]:
        ws_str = str(workspace_dir.resolve())
        static_res = call_mcp_json("mcp__mcp_exec__run_static_qa", {"workspace_dir": ws_str, "file_paths": built_paths})
        py_entry = next((p for p in built_paths if Path(p).name.lower() == "main.py"), None)
        exec_res = (
            call_mcp_json("mcp__mcp_exec__run_sandbox_exec", {"workspace_dir": ws_str, "entrypoint": py_entry})
            if py_entry
            else {"success": True}
        )

        qa_errors: List[Tuple[str, str]] = []
        for err in (static_res.get("errors") or []):
            qa_errors.append((err.get("file") or (built_paths[0] if built_paths else ""), f"[{err.get('gate')}] {err.get('error')}"))
        if not exec_res.get("success"):
            qa_errors.append((exec_res.get("file") or py_entry or "", f"[RUNTIME] {exec_res.get('stacktrace', 'Error de ejecución')}"))

        return qa_errors, (static_res.get("checks_run") or ["ok"])

    def verify_optional_vision(self, workspace_dir: Path, html_entry: str) -> Dict[str, Any]:
        if not self.enable_runtime_vision:
            return {"success": True, "skipped": True, "engine": "disabled_by_flag", "issues": []}
        ws_str = str(workspace_dir.resolve())
        return call_mcp_json("mcp__mcp_runtime_vision__verify_web_runtime", {"workspace_dir": ws_str, "html_file": html_entry})
