# -*- coding: utf-8 -*-
"""
core/pipeline/assembler.py — Ensamblador de Costuras (Wiring) y Bucle de Reparación Acotado.
"""

from pathlib import Path
from typing import Any, Dict, Generator, List

from core.cloud_workers import _heal_code_with_local_qwen, extract_pure_code_from_llm
from core.pipeline.context_budget import ContextBudgetContract, ContextSection, DEFAULT_CONTEXT_BUDGET_TOKENS
from core.pipeline.planner import call_mcp_json
from core.pipeline.verifier import DeterministicVerifier


class WiringAssembler:
    """Coordina la validación integral de costuras (wiring) y repara quirúrgicamente el archivo culpable."""

    def __init__(
        self,
        verifier: DeterministicVerifier,
        local_port: int = 8085,
        max_qa_rounds: int = 3,
        context_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
    ):
        self.verifier = verifier
        self.local_port = local_port
        self.max_qa_rounds = max_qa_rounds
        self.budget = ContextBudgetContract(max_tokens=context_tokens)

    def verify_and_assemble(
        self,
        workspace_dir: Path,
        plan_files: List[Dict[str, str]],
        built_paths: List[str],
        is_aborted=None,
    ) -> Generator[Dict[str, Any], None, None]:
        ws_str = str(workspace_dir.resolve())
        aborted = is_aborted or (lambda: False)
        qa_passed = False
        qa_summary = ""
        rounds_used = 0

        for round_idx in range(1, self.max_qa_rounds + 1):
            if aborted():
                break
            rounds_used = round_idx

            yield {
                "type": "step",
                "id": f"qa_static_{round_idx}",
                "status": "running",
                "title": f"[FASE 4: QA_STATIC R{round_idx}] Verificación determinista (AST/Ruff/Node/Wiring/Eventos)...",
                "detail": "Validando sintaxis, contratos DOM/API, handlers de eventos y ejecución sandbox",
            }
            qa_errors, checks_run = self.verifier.verify_static(workspace_dir, built_paths)
            yield {
                "type": "step",
                "id": f"qa_static_{round_idx}",
                "status": "done",
                "title": f"[FASE 4: QA_STATIC R{round_idx}] {'OK (0 errores)' if not qa_errors else f'{len(qa_errors)} fallo(s)'}",
                "detail": ", ".join(checks_run),
            }

            # Fase 5 (MCP-RUNTIME-VISION): opcional bajo flag, fuera del camino crítico por defecto
            has_html = any(p.lower().endswith((".html", ".htm")) for p in built_paths)
            if has_html and not qa_errors and self.verifier.enable_runtime_vision:
                html_entry = next(p for p in built_paths if p.lower().endswith((".html", ".htm")))
                yield {
                    "type": "step",
                    "id": f"qa_vis_{round_idx}",
                    "status": "running",
                    "title": f"[FASE 5: QA_VIS R{round_idx}] Verificando runtime web opcional (Playwright)...",
                    "detail": f"Probando {html_entry}",
                }
                vis_res = self.verifier.verify_optional_vision(workspace_dir, html_entry)
                if not vis_res.get("success"):
                    for iss in (vis_res.get("issues") or []):
                        qa_errors.append((html_entry, f"[RUNTIME_VISION] {iss}"))
                yield {
                    "type": "step",
                    "id": f"qa_vis_{round_idx}",
                    "status": "done",
                    "title": f"[FASE 5: QA_VIS R{round_idx}] {'OK' if vis_res.get('success') else 'Fallos detectados'} ({vis_res.get('engine', 'probe')})",
                    "detail": "; ".join(vis_res.get("issues") or ["Runtime verificado"]),
                }

            if not qa_errors:
                qa_passed = True
                qa_summary = f"Todos los gates deterministas aprobados en ronda {round_idx}."
                break

            if round_idx < self.max_qa_rounds:
                culprit_path, stack_msg = qa_errors[0]
                culprit_fp = (workspace_dir / culprit_path).resolve()
                if culprit_fp.exists():
                    broken_code = culprit_fp.read_text(encoding="utf-8", errors="replace")
                    skel_res = call_mcp_json("mcp__mcp_ast__get_repo_skeleton", {"workspace_dir": ws_str, "max_files": 18})
                    compacted_ctx, _ = self.budget.pack([
                        ContextSection(name="Error Determinista", content=stack_msg, priority=1, max_share=0.25),
                        ContextSection(name="Esqueleto del Repo", content=skel_res.get("skeleton", ""), priority=2, max_share=0.55),
                    ])
                    f_meta = next((f for f in plan_files if f["path"] == culprit_path), {"path": culprit_path, "description": culprit_path})
                    healed = _heal_code_with_local_qwen(
                        broken_code,
                        f_meta,
                        stack_msg,
                        local_port=self.local_port,
                        sibling_signatures=compacted_ctx,
                    )
                    if healed and healed.strip():
                        clean_healed = extract_pure_code_from_llm(healed, culprit_path)
                        w_heal = call_mcp_json("mcp__mcp_ast__write_module", {"workspace_dir": ws_str, "file_path": culprit_path, "content": clean_healed})
                        if not w_heal.get("success"):
                            call_mcp_json("mcp__mcp_ast__write_module", {"workspace_dir": ws_str, "file_path": culprit_path, "content": clean_healed, "allow_placeholders": True})
                        yield {"type": "file_code", "path": culprit_path, "code": clean_healed, "healed": True}
            else:
                qa_summary = f"Finalizado con avisos tras {self.max_qa_rounds} rondas: {qa_errors[0][1][:160]}"

        yield {
            "type": "assembly_result",
            "passed": qa_passed,
            "summary": qa_summary,
            "rounds_used": rounds_used,
        }
