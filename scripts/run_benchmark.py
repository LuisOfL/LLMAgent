# -*- coding: utf-8 -*-
"""
scripts/run_benchmark.py — Runner automatizado del Benchmark de 20 Apps para LLMAgent.

Uso:
  python scripts/run_benchmark.py --mode dry-run          # Verifica router de 5 vías, contratos y QA determinista sin GPU
  python scripts/run_benchmark.py --mode live --limit 5   # Ejecuta el pipeline real contra el LLM activo y guarda métricas
"""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.intent_router import classify_user_intent
from core.pipeline.context_budget import ContextBudgetContract, ContextSection
from mcp_servers.mcp_exec import _run_static_qa

BENCHMARK_FILE = BASE_DIR / "benchmarks" / "app_benchmark_20.json"
REPORT_FILE = BASE_DIR / "benchmarks" / "last_report.json"


def _seed_sample_repo(ws: Path):
    (ws / "index.html").write_text(
        "<!DOCTYPE html><html><body><canvas id='scene'></canvas><form id='loginForm'><input id='email'></form><script src='app.js'></script></body></html>\n",
        encoding="utf-8",
    )
    (ws / "style.css").write_text("body { background: #0a0c10; color: #fff; }\n", encoding="utf-8")
    (ws / "app.js").write_text(
        "document.addEventListener('DOMContentLoaded', () => { console.log('ready'); });\n",
        encoding="utf-8",
    )


def run_dry_benchmark(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Valida determinísticamente clasificación de intención, presupuesto 16k y gates de QA sobre los 20 casos."""
    t0 = time.time()
    results = []
    intent_ok = 0
    qa_ok = 0

    for c in cases:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            if c.get("requires_existing_repo"):
                _seed_sample_repo(ws)
            intent_res = classify_user_intent(c["prompt"], workspace_dir=ws, active_file="app.js" if c.get("requires_existing_repo") else None)
            matched_intent = intent_res["intent"] == c["expected_intent"]
            if matched_intent:
                intent_ok += 1

            # Verificar contrato de presupuesto 16k
            budget = ContextBudgetContract(max_tokens=16384)
            _, tele = budget.pack([ContextSection(name="Prompt", content=c["prompt"], priority=1)])
            assert tele["budget_tokens"] == 16384

            # Verificar QA determinista sobre repo sembrado si aplica
            if c.get("requires_existing_repo"):
                qa_res = _run_static_qa({"workspace_dir": str(ws), "file_paths": ["index.html", "style.css", "app.js"]})
                case_qa_ok = bool(qa_res.get("success"))
            else:
                case_qa_ok = True
            if case_qa_ok:
                qa_ok += 1

            results.append({
                "id": c["id"],
                "expected_intent": c["expected_intent"],
                "actual_intent": intent_res["intent"],
                "intent_ok": matched_intent,
                "qa_ok": case_qa_ok,
            })

    total = max(1, len(cases))
    report = {
        "mode": "dry-run",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_cases": len(cases),
        "intent_accuracy_pct": round(100.0 * intent_ok / total, 1),
        "deterministic_qa_pass_pct": round(100.0 * qa_ok / total, 1),
        "elapsed_s": round(time.time() - t0, 3),
        "results": results,
    }
    return report


def run_live_benchmark(cases: List[Dict[str, Any]], local_port: int, use_cloud: bool, context_tokens: int) -> Dict[str, Any]:
    """Ejecuta el pipeline real contra el LLM activo y mide tasas reales de arranque, QA, rondas, tokens y tiempo."""
    from core.cloud_workers import load_cloud_config
    from core.mcp_orchestrator import run_elite_mcp_pipeline, run_incremental_iteration

    cloud_cfg = load_cloud_config()
    results = []
    passed_qa = 0
    passed_criteria = 0
    total_nodes = 0
    repaired_nodes = 0
    total_est_tokens = 0
    total_time = 0.0

    for c in cases:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            if c.get("requires_existing_repo"):
                _seed_sample_repo(ws)

            t_start = time.time()
            intent_res = classify_user_intent(c["prompt"], workspace_dir=ws, active_file="app.js" if c.get("requires_existing_repo") else None)
            stream = (
                run_incremental_iteration(c["prompt"], ws, cloud_cfg, use_cloud, local_port=local_port, active_file="app.js", context_tokens=context_tokens)
                if intent_res["intent"] == "iterate_app"
                else run_elite_mcp_pipeline(c["prompt"], ws, cloud_cfg, use_cloud, local_port=local_port, context_tokens=context_tokens, enable_runtime_vision=False)
            )
            done_ev: Dict[str, Any] = {}
            generated_chars = 0
            for ev in stream:
                if ev.get("type") == "file_code":
                    generated_chars += len(ev.get("code", ""))
                elif ev.get("type") == "done":
                    done_ev = ev

            dt = round(time.time() - t_start, 2)
            total_time += dt
            est_tokens = generated_chars // 4
            total_est_tokens += est_tokens

            files_written = done_ev.get("files") or []
            qa_pass = bool(done_ev.get("passed"))
            if qa_pass:
                passed_qa += 1

            # Evaluar criterios de aceptación de la app
            combined_code = "\n".join(
                (ws / f).read_text(encoding="utf-8", errors="replace")
                for f in files_written
                if (ws / f).exists()
            )
            req_ok = all(pat.lower() in combined_code.lower() for pat in c.get("required_patterns", []))
            forb_ok = all(pat not in combined_code for pat in c.get("forbidden_patterns", []))
            criteria_ok = qa_pass and req_ok and forb_ok
            if criteria_ok:
                passed_criteria += 1

            m = done_ev.get("metrics") or {}
            total_nodes += int(m.get("total_nodes", len(files_written)))
            repaired_nodes += int(m.get("nodes_needing_repair", 0))

            results.append({
                "id": c["id"],
                "intent": intent_res["intent"],
                "qa_passed": qa_pass,
                "criteria_passed": criteria_ok,
                "files": files_written,
                "est_tokens": est_tokens,
                "elapsed_s": dt,
                "metrics": m,
            })

    n = max(1, len(cases))
    return {
        "mode": "live",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_cases": len(cases),
        "apps_qa_pass_pct": round(100.0 * passed_qa / n, 1),
        "apps_criteria_pass_pct": round(100.0 * passed_criteria / n, 1),
        "nodes_repaired_pct": round(100.0 * repaired_nodes / max(1, total_nodes), 1),
        "avg_tokens_per_app": int(total_est_tokens / n),
        "avg_time_s": round(total_time / n, 2),
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description="Benchmark automatizado de 20 apps para LLMAgent.")
    parser.add_argument("--mode", choices=["dry-run", "live"], default="dry-run")
    parser.add_argument("--limit", type=int, default=20, help="Número máximo de apps a ejecutar")
    parser.add_argument("--port", type=int, default=8085, help="Puerto de llama-server local")
    parser.add_argument("--cloud", action="store_true", help="Usar trabajador cloud en modo live")
    parser.add_argument("--ctx", type=int, default=16384, help="Presupuesto de contexto (16384-32768)")
    args = parser.parse_args()

    data = json.loads(BENCHMARK_FILE.read_text(encoding="utf-8"))
    cases = (data.get("cases") or [])[: args.limit]

    report = (
        run_dry_benchmark(cases)
        if args.mode == "dry-run"
        else run_live_benchmark(cases, local_port=args.port, use_cloud=args.cloud, context_tokens=args.ctx)
    )
    REPORT_FILE.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n=== LLMAgent Benchmark ({report['mode']}) — {report['total_cases']} casos ===")
    for k, v in report.items():
        if k != "results":
            print(f"  {k}: {v}")
    print(f"Reporte guardado en: {REPORT_FILE}")

    if args.mode == "dry-run" and (report["intent_accuracy_pct"] < 100.0 or report["deterministic_qa_pass_pct"] < 100.0):
        sys.exit(1)


if __name__ == "__main__":
    main()
