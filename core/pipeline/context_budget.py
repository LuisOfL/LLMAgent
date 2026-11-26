# -*- coding: utf-8 -*-
"""
core/pipeline/context_budget.py — Contrato explícito de presupuesto de contexto (16k–32k tokens).

Define qué secciones entran al contexto, con qué prioridad se conservan y qué se poda,
devolviendo telemetría auditable para depurar prompts en lugar de una caja negra.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


DEFAULT_CONTEXT_BUDGET_TOKENS = 16384
MAX_CONTEXT_BUDGET_TOKENS = 32768


@dataclass
class ContextSection:
    name: str
    content: str
    priority: int = 2  # 1 = crítica (errores/contratos/assets), 2 = importante (esqueleto), 3 = auxiliar
    max_share: float = 0.45  # fracción máxima del presupuesto asignable a esta sección


@dataclass
class ContextBudgetContract:
    """Contrato explícito y depurable para armado y poda de contexto."""

    max_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS
    reserve_ratio: float = 0.55  # 55% para contexto de entrada, 45% reservado para system prompt + salida
    chars_per_token: float = 3.5

    def max_input_chars(self) -> int:
        clamped_tokens = max(4096, min(int(self.max_tokens), MAX_CONTEXT_BUDGET_TOKENS))
        return max(4000, int(clamped_tokens * self.reserve_ratio * self.chars_per_token))

    def pack(self, sections: List[ContextSection]) -> Tuple[str, Dict[str, object]]:
        """Empaqueta secciones ordenadas por prioridad respetando el presupuesto y reportando telemetría."""
        budget_chars = self.max_input_chars()
        ordered = sorted((s for s in sections if s.content and s.content.strip()), key=lambda s: s.priority)

        parts: List[str] = []
        used = 0
        included: List[str] = []
        truncated: List[str] = []
        dropped: List[str] = []

        for sec in ordered:
            remaining = budget_chars - used
            if remaining < 200:
                dropped.append(sec.name)
                continue
            cap = min(remaining, max(600, int(budget_chars * sec.max_share)))
            txt = sec.content.strip()
            if len(txt) > cap:
                head = int(cap * 0.6)
                tail = max(0, cap - head - 48)
                txt = txt[:head] + f"\n... [Poda explícita en `{sec.name}`] ...\n" + (txt[-tail:] if tail else "")
                truncated.append(sec.name)
            parts.append(f"### [{sec.name}]\n{txt}")
            used += len(txt)
            included.append(sec.name)

        telemetry = {
            "budget_tokens": self.max_tokens,
            "budget_chars": budget_chars,
            "used_chars": used,
            "est_tokens": int(used / self.chars_per_token),
            "included": included,
            "truncated": truncated,
            "dropped": dropped,
        }
        return "\n\n".join(parts), telemetry


def compact_context_for_kv_budget(
    text: str,
    max_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
    reserve_ratio: float = 0.55,
) -> str:
    """Helper compatible hacia atrás que aplica el contrato explícito (default 16,384 tokens)."""
    if not text:
        return ""
    contract = ContextBudgetContract(max_tokens=max_tokens, reserve_ratio=reserve_ratio)
    max_chars = contract.max_input_chars()
    if len(text) <= max_chars:
        return text
    head_len = int(max_chars * 0.55)
    tail_len = max(0, max_chars - head_len - 72)
    return (
        text[:head_len]
        + f"\n\n... [Contexto compactado bajo contrato <= {max_tokens} tokens] ...\n\n"
        + (text[-tail_len:] if tail_len else "")
    )
