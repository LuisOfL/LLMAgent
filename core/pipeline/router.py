# -*- coding: utf-8 -*-
"""
core/pipeline/router.py — Circuit Breaker y Router de Ejecución basado en Calidad, Latencia y Disponibilidad.
"""

import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.cloud_workers import (
    build_bedrock_worker_prompt,
    call_cloud_worker,
    is_refusal_response,
    recovery_handler,
    generate_file_with_local_orchestrator,
)


class CircuitBreaker:
    """
    Circuit Breaker operativo orientado a SLO (latencia, disponibilidad, rate-limit y errores de proveedor).
    Abre el circuito tras `failure_threshold` fallos consecutivos y reintenta tras `cooldown_seconds`.
    """

    def __init__(self, failure_threshold: int = 2, cooldown_seconds: float = 90.0, max_latency_ms: float = 45000.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.max_latency_ms = max_latency_ms
        self.failures = 0
        self.opened_at: Optional[float] = None
        self.last_reason: str = ""
        self.latencies_ms: List[float] = []

    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if (time.time() - self.opened_at) >= self.cooldown_seconds:
            self.reset()
            return False
        return True

    def record_success(self, latency_ms: float = 0.0):
        self.failures = 0
        self.opened_at = None
        self.last_reason = ""
        if latency_ms > 0:
            self.latencies_ms.append(round(latency_ms, 1))
            if len(self.latencies_ms) > 30:
                self.latencies_ms = self.latencies_ms[-30:]

    def record_failure(self, reason: str, immediate_trip: bool = False):
        self.last_reason = reason
        self.failures += self.failure_threshold if immediate_trip else 1
        if self.failures >= self.failure_threshold:
            self.opened_at = time.time()

    def avg_latency_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        return round(sum(self.latencies_ms) / len(self.latencies_ms), 1)

    def reset(self):
        self.failures = 0
        self.opened_at = None
        self.last_reason = ""


class ExecutionRouter:
    """Enruta cada módulo entre trabajador Cloud (calidad/velocidad) y motor Local (disponibilidad/coste $0)."""

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
            t0 = time.time()
            prompt = build_bedrock_worker_prompt(
                file_spec,
                general_context=compacted_context,
                workspace_dir=workspace_dir,
            )
            ok, resp = call_cloud_worker(prompt, self.cloud_cfg, bypass_desensitize=True)
            elapsed_ms = (time.time() - t0) * 1000.0

            if ok and resp and not is_refusal_response(resp):
                self.breaker.record_success(latency_ms=elapsed_ms)
                prov = self.cloud_cfg.get("provider", "cloud").upper()
                return resp, f"{prov} ({int(elapsed_ms)}ms)", False

            reason = "ProviderPolicy/Empty" if (resp and is_refusal_response(resp)) else "Timeout/RateLimit/Offline"
            self.breaker.record_failure(reason, immediate_trip=False)
            ok_loc, loc_resp = recovery_handler(
                file_spec,
                failure_reason=reason,
                local_port=self.local_port,
                general_context=compacted_context,
                workspace_dir=workspace_dir,
            )
            return (loc_resp if ok_loc and loc_resp else ""), f"Local Fallback ({reason})", True

        ok_loc, loc_resp = generate_file_with_local_orchestrator(
            file_spec,
            local_port=self.local_port,
            general_context=compacted_context,
            workspace_dir=workspace_dir,
        )
        return (loc_resp if ok_loc and loc_resp else ""), "Local Coder (Coste $0)", False
