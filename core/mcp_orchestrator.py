# -*- coding: utf-8 -*-
"""
core/mcp_orchestrator.py — Orquestador Central de Élite basado en Máquina de Estados + 5 Servidores MCP Reales.

Arquitectura:
  PLAN (GBNF + MCP-ARCH)
    -> ASSETS (MCP-ASSETS)
    -> BUILD (Circuit Breaker Router: Bedrock -> Local Uncensored + MCP-AST)
    -> QA_STATIC (MCP-EXEC)
    -> QA_VIS (MCP-RUNTIME-VISION)
    -> [Si falla y ronda < 3: regresa a BUILD con StackTrace exacto]
    -> DONE (Entrega en Monaco + Preview)
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from core.mcp_client import mcp_client_manager
from core.local_codex_engine import (
    detect_app_archetype,
    plan_blueprint_constrained,
    expand_stub_functions_surgically,
)
from core.cloud_workers import (
    build_bedrock_worker_prompt,
    call_cloud_worker,
    is_refusal_response,
    recovery_handler,
    generate_file_with_local_orchestrator,
    verify_and_heal_code,
    auto_fix_common_python_issues,
    _heal_code_with_local_qwen,
)


# ==============================================================================
# 1. CONTEXT COMPACTION (PRESUPUESTO KV-CACHE <= 8192 TOKENS)
# ==============================================================================

def compact_context_for_kv_budget(text: str, max_tokens: int = 8192, reserve_ratio: float = 0.35) -> str:
    """
    Mantiene el contexto acumulado dentro del presupuesto de KV-Cache (<= 8192 tokens)
    reservando espacio para el prompt del sistema y la generación del archivo actual.
    """
    if not text:
        return ""
    max_ctx_tokens = int(max_tokens * reserve_ratio)
    max_chars = max(1600, max_ctx_tokens * 3)
    if len(text) <= max_chars:
        return text
    head_len = int(max_chars * 0.45)
    tail_len = max_chars - head_len - 60
    return text[:head_len] + "\n\n... [Contexto compactado para KV-Cache <= 8192 tokens] ...\n\n" + text[-tail_len:]


# ==============================================================================
# 2. CIRCUIT BREAKER & FALLBACK ROUTER (AWS BEDROCK -> LOCAL UNCENSORED)
# ==============================================================================

class CircuitBreaker:
    """Abre el circuito si el worker en la nube falla/rechaza/throttlea 2 veces seguidas."""

    def __init__(self, failure_threshold: int = 2, cooldown_seconds: float = 90.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.failures = 0
        self.opened_at: Optional[float] = None
        self.last_reason: str = ""

    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if (time.time() - self.opened_at) >= self.cooldown_seconds:
            self.reset()
            return False
        return True

    def record_success(self):
        self.failures = 0
        self.opened_at = None
        self.last_reason = ""

    def record_failure(self, reason: str, immediate_trip: bool = False):
        self.last_reason = reason
        self.failures += self.failure_threshold if immediate_trip else 1
        if self.failures >= self.failure_threshold:
            self.opened_at = time.time()

    def reset(self):
        self.failures = 0
        self.opened_at = None
        self.last_reason = ""


class ExecutionRouter:
    """Enruta la generación de cada módulo entre AWS Bedrock (Frontier) y Qwen Local Uncensored."""

    def __init__(self, cloud_cfg: Dict[str, Any], use_cloud: bool, local_port: int):
        self.cloud_cfg = cloud_cfg
        self.use_cloud = use_cloud
        self.local_port = local_port
        self.breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=90.0)

    def generate_module(
        self,
        file_spec: Dict[str, str],
        compacted_context: str,
        workspace_dir: Path,
    ) -> Tuple[str, str, bool]:
        """Retorna (codigo_raw, etiqueta_builder, uso_fallback)."""
        if self.use_cloud and not self.breaker.is_open():
            prompt = build_bedrock_worker_prompt(
                file_spec,
                general_context=compacted_context,
                workspace_dir=workspace_dir,
            )
            ok, resp = call_cloud_worker(prompt, self.cloud_cfg, bypass_desensitize=True)
            if ok and resp and not is_refusal_response(resp):
                self.breaker.record_success()
                prov = self.cloud_cfg.get("provider", "cloud").upper()
                return resp, f"{prov} (Frontier Coder)", False

            reason = "Guardrail/Refusal" if (resp and is_refusal_response(resp)) else "Offline/Throttling/Error"
            self.breaker.record_failure(reason, immediate_trip=("Refusal" in reason))
            ok_loc, loc_resp = recovery_handler(
                file_spec,
                failure_reason=reason,
                local_port=self.local_port,
                general_context=compacted_context,
                workspace_dir=workspace_dir,
            )
            return (loc_resp if ok_loc and loc_resp else ""), f"Qwen Local (Fallback: {reason})", True

        ok_loc, loc_resp = generate_file_with_local_orchestrator(
            file_spec,
            local_port=self.local_port,
            general_context=compacted_context,
            workspace_dir=workspace_dir,
        )
        return (loc_resp if ok_loc and loc_resp else ""), "Qwen Local (100% Privado)", False


# ==============================================================================
# 3. ORQUESTADOR CENTRAL DE MÁQUINA DE ESTADOS (PLAN -> ASSETS -> BUILD -> QA)
# ==============================================================================

def _call_mcp_json(namespaced_tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
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


def run_elite_mcp_pipeline(
    user_query: str,
    workspace_dir: Path,
    cloud_cfg: Dict[str, Any],
    use_cloud: bool,
    local_port: int = 8085,
    max_qa_rounds: int = 3,
    is_aborted=None,
) -> Generator[Dict[str, Any], None, None]:
    """
    Ejecuta la máquina de estados completa:
      PLAN -> ASSETS -> BUILD -> QA_STATIC -> QA_VIS -> (Retry BUILD si falla) -> DONE
    Emite eventos estructurados (`step`, `file_code`, `done`) para streaming SSE en `server.py`.
    """
    ws_str = str(workspace_dir.resolve())
    aborted = is_aborted or (lambda: False)

    # Asegurar que los 5 servidores MCP estén conectados
    if not any(s.get("connected") for s in mcp_client_manager.get_status()):
        mcp_client_manager.reload_servers()

    # ------------------------------------------------------------------
    # FASE 1: PLAN (LLM Local GBNF + MCP-ARCH)
    # ------------------------------------------------------------------
    yield {"type": "step", "id": "phase_plan", "status": "running", "title": "[FASE 1: PLAN] Diseñando plano DAG y contratos (GBNF + MCP-ARCH)...", "detail": "Clasificando arquetipo y orden topológico"}
    archetype = detect_app_archetype(user_query, workspace_dir)
    plan_summary, plan_files = plan_blueprint_constrained(user_query, local_port=local_port, workspace_dir=workspace_dir)

    dag_res = _call_mcp_json("mcp__mcp_arch__build_dag_plan", {"archetype": archetype, "files": plan_files})
    if dag_res.get("success") and dag_res.get("ordered_files"):
        plan_files = dag_res["ordered_files"]

    file_list_str = ", ".join(f["path"] for f in plan_files)
    yield {"type": "step", "id": "phase_plan", "status": "done", "title": f"[FASE 1: PLAN] {archetype} ({len(plan_files)} archivos)", "detail": f"DAG: {file_list_str}"}

    # ------------------------------------------------------------------
    # FASE 2: ASSETS (MCP-ASSETS)
    # ------------------------------------------------------------------
    yield {"type": "step", "id": "phase_assets", "status": "running", "title": "[FASE 2: ASSETS] Consultando MCP-ASSETS (3D/UI/Data + SecKB)...", "detail": "Verificando assets descargables y contexto técnico"}
    asset_res = _call_mcp_json("mcp__mcp_assets__fetch_project_assets", {"workspace_dir": ws_str, "query": user_query, "auto_download": True})
    sec_res = _call_mcp_json("mcp__mcp_assets__query_sec_kb", {"query": user_query})

    extra_context_parts: List[str] = []
    if asset_res.get("downloaded"):
        dl = asset_res["downloaded"]
        extra_context_parts.append(f"ASSET DESCARGADO EN WORKSPACE: ./{dl['filename']} ({dl['size_bytes']} bytes)")
        asset_detail = f"Asset descargado: {dl['filename']}"
    else:
        asset_detail = "Sin descarga externa requerida"
    if sec_res.get("active") and sec_res.get("context"):
        extra_context_parts.append(sec_res["context"])
    yield {"type": "step", "id": "phase_assets", "status": "done", "title": "[FASE 2: ASSETS] Completada", "detail": asset_detail}

    # ------------------------------------------------------------------
    # FASE 3: BUILD (ExecutionRouter con Circuit Breaker + MCP-AST)
    # ------------------------------------------------------------------
    router = ExecutionRouter(cloud_cfg=cloud_cfg, use_cloud=use_cloud, local_port=local_port)
    built_paths: List[str] = []
    total_files = len(plan_files)

    for idx, f_spec in enumerate(plan_files, 1):
        if aborted():
            break
        f_path = f_spec["path"]
        step_id = f"phase_build_{idx}"
        yield {"type": "step", "id": step_id, "status": "running", "title": f"[FASE 3: BUILD {idx}/{total_files}] Generando {f_path}...", "detail": f_spec.get("description", "")}

        # Obtener contratos reales de los archivos ya construidos vía MCP-ARCH + compactar para KV-Cache <= 8192
        contracts_res = _call_mcp_json("mcp__mcp_arch__extract_contracts", {"workspace_dir": ws_str, "file_paths": built_paths}) if built_paths else {}
        raw_ctx = "\n\n".join(extra_context_parts + ([contracts_res.get("contracts_text", "")] if contracts_res.get("contracts_text") else []))
        compacted_ctx = compact_context_for_kv_budget(raw_ctx, max_tokens=8192)

        raw_code, builder_label, _ = router.generate_module(f_spec, compacted_ctx, workspace_dir)
        pure_code, is_valid, verify_note = verify_and_heal_code(
            raw_code or f"# {f_path}\n",
            f_spec,
            cloud_cfg,
            local_port=local_port,
            workspace_dir=workspace_dir,
            sibling_signatures=compacted_ctx,
            use_cloud=use_cloud and not router.breaker.is_open(),
        )
        if f_path.lower().endswith(".py") and is_valid:
            pure_code, expanded = expand_stub_functions_surgically(pure_code, f_spec, local_port=local_port, sibling_signatures=compacted_ctx, max_functions=3)
            if expanded:
                pure_code = auto_fix_common_python_issues(pure_code, f_path, workspace_dir=workspace_dir)
                verify_note = f"{verify_note} + AST expand ({', '.join(expanded)})"

        # Escribir físicamente vía MCP-AST (stdio)
        _call_mcp_json("mcp__mcp_ast__write_module", {"workspace_dir": ws_str, "file_path": f_path, "content": pure_code})
        built_paths.append(f_path)

        yield {"type": "step", "id": step_id, "status": "done", "title": f"[FASE 3: BUILD {idx}/{total_files}] {f_path} ({builder_label})", "detail": verify_note}
        yield {"type": "file_code", "path": f_path, "code": pure_code}

    # ------------------------------------------------------------------
    # FASE 4 & 5: QA_STATIC (MCP-EXEC) + QA_VIS (MCP-RUNTIME-VISION) + RETRY LOOP (< 3 rondas)
    # ------------------------------------------------------------------
    qa_passed = False
    qa_summary = ""
    for round_idx in range(1, max_qa_rounds + 1):
        if aborted():
            break

        # A) QA_STATIC + Sandbox Exec via MCP-EXEC
        yield {"type": "step", "id": f"qa_static_{round_idx}", "status": "running", "title": f"[FASE 4: QA_STATIC R{round_idx}] Ejecutando MCP-EXEC (AST/Ruff/Node/Sandbox)...", "detail": "Validando sintaxis, contratos DOM y ejecución"}
        static_res = _call_mcp_json("mcp__mcp_exec__run_static_qa", {"workspace_dir": ws_str, "file_paths": built_paths})
        py_entry = next((p for p in built_paths if Path(p).name.lower() == "main.py"), None)
        exec_res = _call_mcp_json("mcp__mcp_exec__run_sandbox_exec", {"workspace_dir": ws_str, "entrypoint": py_entry}) if py_entry else {"success": True}

        qa_errors: List[Tuple[str, str]] = []
        for err in (static_res.get("errors") or []):
            qa_errors.append((err.get("file") or built_paths[0], f"[{err.get('gate')}] {err.get('error')}"))
        if not exec_res.get("success"):
            qa_errors.append((exec_res.get("file") or py_entry or built_paths[0], f"[RUNTIME] {exec_res.get('stacktrace', 'Error de ejecución')}"))

        yield {"type": "step", "id": f"qa_static_{round_idx}", "status": "done", "title": f"[FASE 4: QA_STATIC R{round_idx}] {'OK (0 errores)' if not qa_errors else f'{len(qa_errors)} fallo(s)'}", "detail": ", ".join(static_res.get("checks_run") or ["ok"])}

        # B) QA_VIS via MCP-RUNTIME-VISION (si existe index.html)
        has_html = any(p.lower().endswith((".html", ".htm")) for p in built_paths)
        if has_html and not qa_errors:
            html_entry = next(p for p in built_paths if p.lower().endswith((".html", ".htm")))
            yield {"type": "step", "id": f"qa_vis_{round_idx}", "status": "running", "title": f"[FASE 5: QA_VIS R{round_idx}] Verificando runtime web (MCP-RUNTIME-VISION)...", "detail": f"Probando {html_entry} en servidor efímero"}
            vis_res = _call_mcp_json("mcp__mcp_runtime_vision__verify_web_runtime", {"workspace_dir": ws_str, "html_file": html_entry})
            if not vis_res.get("success"):
                for iss in (vis_res.get("issues") or []):
                    qa_errors.append((html_entry, f"[RUNTIME_VISION] {iss}"))
            yield {"type": "step", "id": f"qa_vis_{round_idx}", "status": "done", "title": f"[FASE 5: QA_VIS R{round_idx}] {'OK' if vis_res.get('success') else 'Fallos detectados'} ({vis_res.get('engine', 'probe')})", "detail": "; ".join(vis_res.get("issues") or ["DOM y assets verificados"])}

        if not qa_errors:
            qa_passed = True
            qa_summary = f"Todos los Gates aprobados en ronda {round_idx}."
            break

        # Regresar a BUILD con el StackTrace exacto para auto-reparar
        if round_idx < max_qa_rounds:
            culprit_path, stack_msg = qa_errors[0]
            culprit_fp = (workspace_dir / culprit_path).resolve()
            if culprit_fp.exists():
                broken_code = culprit_fp.read_text(encoding="utf-8", errors="replace")
                contracts_res = _call_mcp_json("mcp__mcp_arch__extract_contracts", {"workspace_dir": ws_str, "file_paths": built_paths})
                compacted_ctx = compact_context_for_kv_budget(contracts_res.get("contracts_text", ""), max_tokens=8192)
                f_meta = next((f for f in plan_files if f["path"] == culprit_path), {"path": culprit_path, "description": culprit_path})
                healed = _heal_code_with_local_qwen(broken_code, f_meta, stack_msg, local_port=local_port, sibling_signatures=compacted_ctx)
                if healed and healed.strip():
                    _call_mcp_json("mcp__mcp_ast__write_module", {"workspace_dir": ws_str, "file_path": culprit_path, "content": healed})
                    yield {"type": "file_code", "path": culprit_path, "code": healed, "healed": True}
        else:
            qa_summary = f"Finalizado con avisos tras {max_qa_rounds} rondas: {qa_errors[0][1][:160]}"

    yield {
        "type": "done",
        "passed": qa_passed,
        "summary": qa_summary,
        "files": built_paths,
        "archetype": archetype,
    }
