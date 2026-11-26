# -*- coding: utf-8 -*-
"""
core/pipeline/ — Pipeline modular desacoplado de LLMAgent.

Separa responsabilidades en módulos independientes y testeables:
  - context_budget.py : Contrato explícito de presupuesto de contexto (16k-32k tokens)
  - router.py         : Circuit Breaker y Router orientado a calidad/latencia/coste
  - planner.py        : Planificador DAG (GBNF + MCP-ARCH + MCP-ASSETS)
  - builder.py        : Constructor por nodo (NodeBuilder) e Iterador incremental (IncrementalIterator)
  - verifier.py       : Verificador determinista (MCP-EXEC) + Runtime Vision opcional bajo flag
  - assembler.py      : Ensamblador de costuras entre archivos y ciclo de reparación acotado
"""

from core.pipeline.context_budget import ContextBudgetContract, ContextSection, compact_context_for_kv_budget
from core.pipeline.router import CircuitBreaker, ExecutionRouter
from core.pipeline.planner import AppPlanner, call_mcp_json
from core.pipeline.builder import NodeBuilder, IncrementalIterator
from core.pipeline.verifier import DeterministicVerifier
from core.pipeline.assembler import WiringAssembler

__all__ = [
    "ContextBudgetContract",
    "ContextSection",
    "compact_context_for_kv_budget",
    "CircuitBreaker",
    "ExecutionRouter",
    "AppPlanner",
    "call_mcp_json",
    "NodeBuilder",
    "IncrementalIterator",
    "DeterministicVerifier",
    "WiringAssembler",
]
