# -*- coding: utf-8 -*-
"""
core/mcp_orchestrator.py — Coordinador ligero sobre `core/pipeline/`.

Separa responsabilidades en módulos independientes y testeables:
  - AppPlanner            (core/pipeline/planner.py)
  - NodeBuilder           (core/pipeline/builder.py)
  - IncrementalIterator   (core/pipeline/builder.py)   -> ruta `iterate_app`
  - DeterministicVerifier (core/pipeline/verifier.py)  -> QA estático + Runtime Vision opcional bajo flag
  - WiringAssembler       (core/pipeline/assembler.py) -> costuras y reparación acotada
  - ExecutionRouter       (core/pipeline/router.py)    -> Circuit Breaker por calidad/latencia/disponibilidad
  - ContextBudgetContract (core/pipeline/context_budget.py) -> contrato explícito 16k–32k tokens
"""

from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from core.mcp_client import mcp_client_manager
from core.pipeline import (
    AppPlanner,
    CircuitBreaker,
    ContextBudgetContract,
    DeterministicVerifier,
    ExecutionRouter,
    IncrementalIterator,
    NodeBuilder,
    WiringAssembler,
    call_mcp_json as _call_mcp_json,
    compact_context_for_kv_budget,
)
from core.pipeline.context_budget import DEFAULT_CONTEXT_BUDGET_TOKENS


def _ensure_mcp_connected():
    if not any(s.get("connected") for s in mcp_client_manager.get_status()):
        mcp_client_manager.reload_servers()


def run_incremental_iteration(
    user_query: str,
    workspace_dir: Path,
    cloud_cfg: Dict[str, Any],
    use_cloud: bool,
    local_port: int = 8085,
    active_file: Optional[str] = None,
    context_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
    is_aborted=None,
) -> Generator[Dict[str, Any], None, None]:
    """Ejecuta la ruta de primera clase `iterate_app` (parcheo incremental con MCP-AST sin regenerar todo)."""
    _ensure_mcp_connected()
    router = ExecutionRouter(cloud_cfg=cloud_cfg, use_cloud=use_cloud, local_port=local_port)
    iterator = IncrementalIterator(
        router=router,
        cloud_cfg=cloud_cfg,
        use_cloud=use_cloud,
        local_port=local_port,
        context_tokens=context_tokens,
    )
    yield from iterator.iterate(
        user_query=user_query,
        workspace_dir=workspace_dir,
        active_file=active_file,
        is_aborted=is_aborted,
    )


def run_elite_mcp_pipeline(
    user_query: str,
    workspace_dir: Path,
    cloud_cfg: Dict[str, Any],
    use_cloud: bool,
    local_port: int = 8085,
    max_qa_rounds: int = 3,
    context_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
    enable_runtime_vision: bool = False,
    is_aborted=None,
) -> Generator[Dict[str, Any], None, None]:
    """
    Coordina los módulos desacoplados:
      PLAN -> ASSETS -> BUILD (nodo a nodo) -> QA_STATIC + WIRING -> [QA_VIS opcional bajo flag] -> DONE
    """
    aborted = is_aborted or (lambda: False)
    _ensure_mcp_connected()

    planner = AppPlanner(local_port=local_port)
    router = ExecutionRouter(cloud_cfg=cloud_cfg, use_cloud=use_cloud, local_port=local_port)
    builder = NodeBuilder(
        router=router,
        cloud_cfg=cloud_cfg,
        use_cloud=use_cloud,
        local_port=local_port,
        context_tokens=context_tokens,
    )
    verifier = DeterministicVerifier(enable_runtime_vision=enable_runtime_vision)
    assembler = WiringAssembler(
        verifier=verifier,
        local_port=local_port,
        max_qa_rounds=max_qa_rounds,
        context_tokens=context_tokens,
    )

    # FASE 1: PLAN
    yield {"type": "step", "id": "phase_plan", "status": "running", "title": "[FASE 1: PLAN] Diseñando plano DAG y contratos (GBNF + MCP-ARCH)...", "detail": "Clasificando arquetipo y orden topológico"}
    archetype, _plan_summary, plan_files = planner.plan_dag(user_query, workspace_dir)
    file_list_str = ", ".join(f["path"] for f in plan_files)
    yield {"type": "step", "id": "phase_plan", "status": "done", "title": f"[FASE 1: PLAN] {archetype} ({len(plan_files)} archivos)", "detail": f"DAG: {file_list_str}"}

    # FASE 2: ASSETS
    yield {"type": "step", "id": "phase_assets", "status": "running", "title": "[FASE 2: ASSETS] Resolviendo assets reales (MCP-ASSETS)...", "detail": "Buscando assets públicos y contexto técnico"}
    extra_context_parts, asset_detail = planner.fetch_assets(user_query, workspace_dir)
    yield {"type": "step", "id": "phase_assets", "status": "done", "title": "[FASE 2: ASSETS] Completada", "detail": asset_detail}

    # FASE 3: BUILD (nodo a nodo)
    built_paths: List[str] = []
    nodes_needing_repair = 0
    total_files = len(plan_files)
    for idx, f_spec in enumerate(plan_files, 1):
        if aborted():
            break
        f_path = f_spec["path"]
        step_id = f"phase_build_{idx}"
        yield {"type": "step", "id": step_id, "status": "running", "title": f"[FASE 3: BUILD {idx}/{total_files}] Nodo DAG: {f_path}...", "detail": f_spec.get("description", "")}
        pure_code, builder_label, verify_note, needed_repair = builder.build_node(
            f_spec=f_spec,
            workspace_dir=workspace_dir,
            built_paths=built_paths,
            extra_context_parts=extra_context_parts,
        )
        if needed_repair:
            nodes_needing_repair += 1
        built_paths.append(f_path)
        yield {"type": "step", "id": step_id, "status": "done", "title": f"[FASE 3: BUILD {idx}/{total_files}] {f_path} ({builder_label})", "detail": verify_note}
        yield {"type": "file_code", "path": f_path, "code": pure_code}

    # FASE 4 (+ FASE 5 opcional): VERIFICACIÓN DETERMINISTA Y ENSAMBLADO DE COSTURAS
    qa_passed = False
    qa_summary = ""
    rounds_used = 1
    for ev in assembler.verify_and_assemble(workspace_dir, plan_files, built_paths, is_aborted=aborted):
        if ev.get("type") == "assembly_result":
            qa_passed = bool(ev.get("passed"))
            qa_summary = str(ev.get("summary", ""))
            rounds_used = int(ev.get("rounds_used", 1))
        else:
            yield ev

    yield {
        "type": "done",
        "passed": qa_passed,
        "summary": qa_summary,
        "files": built_paths,
        "archetype": archetype,
        "metrics": {
            "total_nodes": total_files,
            "nodes_needing_repair": nodes_needing_repair,
            "qa_rounds": rounds_used,
            "context_budget_tokens": context_tokens,
            "runtime_vision_enabled": verifier.enable_runtime_vision,
        },
    }


__all__ = [
    "CircuitBreaker",
    "ContextBudgetContract",
    "ExecutionRouter",
    "compact_context_for_kv_budget",
    "_call_mcp_json",
    "run_elite_mcp_pipeline",
    "run_incremental_iteration",
]
