# -*- coding: utf-8 -*-
"""
core/pipeline/builder.py — Constructor por nodo (NodeBuilder) e Iterador Incremental (IncrementalIterator).
"""

import re
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from core.local_codex_engine import expand_stub_functions_surgically
from core.cloud_workers import (
    verify_and_heal_code,
    auto_fix_common_python_issues,
    _heal_code_with_local_qwen,
    extract_pure_code_from_llm,
)
from core.pipeline.context_budget import ContextBudgetContract, ContextSection, DEFAULT_CONTEXT_BUDGET_TOKENS
from core.pipeline.planner import call_mcp_json
from core.pipeline.router import ExecutionRouter


class NodeBuilder:
    """Construye un único nodo del DAG usando contratos explícitos y QA estático determinista inmediato."""

    def __init__(
        self,
        router: ExecutionRouter,
        cloud_cfg: Dict[str, Any],
        use_cloud: bool,
        local_port: int = 8085,
        context_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
    ):
        self.router = router
        self.cloud_cfg = cloud_cfg
        self.use_cloud = use_cloud
        self.local_port = local_port
        self.budget = ContextBudgetContract(max_tokens=context_tokens)

    def build_node(
        self,
        f_spec: Dict[str, str],
        workspace_dir: Path,
        built_paths: List[str],
        extra_context_parts: List[str],
    ) -> Tuple[str, str, str, bool]:
        """Retorna (pure_code, builder_label, verify_note, needed_node_repair)."""
        ws_str = str(workspace_dir.resolve())
        f_path = f_spec["path"]
        needed_repair = False

        contracts_res = (
            call_mcp_json("mcp__mcp_arch__extract_contracts", {"workspace_dir": ws_str, "file_paths": built_paths})
            if built_paths
            else {}
        )

        sections = [
            ContextSection(name="Assets y Especificaciones", content="\n\n".join(extra_context_parts), priority=1, max_share=0.30),
            ContextSection(name="Contratos de Dependencias", content=contracts_res.get("contracts_text", ""), priority=1, max_share=0.65),
        ]
        compacted_ctx, _ = self.budget.pack(sections)

        raw_code, builder_label, _ = self.router.generate_module(f_spec, compacted_ctx, workspace_dir)
        pure_code, is_valid, verify_note = verify_and_heal_code(
            raw_code or f"# {f_path}\n",
            f_spec,
            self.cloud_cfg,
            local_port=self.local_port,
            workspace_dir=workspace_dir,
            sibling_signatures=compacted_ctx,
            use_cloud=self.use_cloud and not self.router.breaker.is_open(),
        )
        if f_path.lower().endswith(".py") and is_valid:
            pure_code, expanded = expand_stub_functions_surgically(
                pure_code,
                f_spec,
                local_port=self.local_port,
                sibling_signatures=compacted_ctx,
                max_functions=3,
            )
            if expanded:
                pure_code = auto_fix_common_python_issues(pure_code, f_path, workspace_dir=workspace_dir)
                verify_note = f"{verify_note} + AST expand ({', '.join(expanded)})"

        # Escritura estricta vía MCP-AST (limpia fences Markdown y rechaza placeholders)
        w_res = call_mcp_json("mcp__mcp_ast__write_module", {"workspace_dir": ws_str, "file_path": f_path, "content": pure_code})
        if not w_res.get("success"):
            needed_repair = True
            healed_trunc = _heal_code_with_local_qwen(
                pure_code,
                f_spec,
                w_res.get("error", "Código truncado"),
                local_port=self.local_port,
                sibling_signatures=compacted_ctx,
            )
            if healed_trunc and healed_trunc.strip():
                pure_code = extract_pure_code_from_llm(healed_trunc, f_path)
            call_mcp_json(
                "mcp__mcp_ast__write_module",
                {"workspace_dir": ws_str, "file_path": f_path, "content": pure_code, "allow_placeholders": True},
            )

        # QA Estático determinista INMEDIATO sobre este nodo (sin LLM)
        node_qa = call_mcp_json("mcp__mcp_exec__run_static_qa", {"workspace_dir": ws_str, "file_paths": [f_path]})
        if not node_qa.get("success") and node_qa.get("errors"):
            needed_repair = True
            err_txt = "; ".join(f"[{e.get('gate')}] {e.get('error')}" for e in node_qa["errors"][:4])
            healed_node = _heal_code_with_local_qwen(
                pure_code,
                f_spec,
                err_txt,
                local_port=self.local_port,
                sibling_signatures=compacted_ctx,
            )
            if healed_node and healed_node.strip():
                pure_code = extract_pure_code_from_llm(healed_node, f_path)
                call_mcp_json(
                    "mcp__mcp_ast__write_module",
                    {"workspace_dir": ws_str, "file_path": f_path, "content": pure_code, "allow_placeholders": True},
                )
                verify_note = f"{verify_note} + Curado determinista"

        return pure_code, builder_label, verify_note, needed_repair


class IncrementalIterator:
    """
    Ruta de primera clase `iterate_app`:
    Edita únicamente los archivos/símbolos necesarios sobre un repositorio existente
    usando `get_repo_skeleton`, `read_symbol`, `patch_symbol` (Python) y edición quirúrgica
    sin regenerar todo el proyecto desde cero.
    """

    CODE_EXTS = (".py", ".js", ".html", ".htm", ".css")

    def __init__(
        self,
        router: ExecutionRouter,
        cloud_cfg: Dict[str, Any],
        use_cloud: bool,
        local_port: int = 8085,
        context_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
    ):
        self.router = router
        self.cloud_cfg = cloud_cfg
        self.use_cloud = use_cloud
        self.local_port = local_port
        self.budget = ContextBudgetContract(max_tokens=context_tokens)

    def select_target_files(
        self,
        user_query: str,
        workspace_dir: Path,
        active_file: Optional[str] = None,
    ) -> List[str]:
        """Selecciona determinísticamente los archivos concretos del repo a iterar (1–3 archivos)."""
        q_low = user_query.lower()
        existing: List[str] = []
        for p in sorted(workspace_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = str(p.relative_to(workspace_dir)).replace("\\", "/")
            if any(part.startswith(".") or part in ("node_modules", "__pycache__", "engine", "models", ".venv", "venv") for part in p.relative_to(workspace_dir).parts):
                continue
            if p.suffix.lower() in self.CODE_EXTS:
                existing.append(rel)

        if not existing:
            return []

        # 1. Archivos mencionados explícitamente en el prompt
        mentioned = [rel for rel in existing if Path(rel).name.lower() in q_low or rel.lower() in q_low]
        if mentioned:
            return mentioned[:3]

        targets: List[str] = []
        # 2. Heurística por intención de edición (estilos vs lógica 3D/JS vs HTML vs backend)
        if any(w in q_low for w in ("color", "fondo", "estilo", "css", "fuente", "diseño", "tema", "dark", "claro")):
            targets.extend([r for r in existing if r.lower().endswith(".css")][:1])
            if not targets:
                targets.extend([r for r in existing if r.lower().endswith((".html", ".htm"))][:1])

        if any(w in q_low for w in ("3d", "glb", "gltf", "modelo", "escena", "luz", "luces", "cámara", "camara", "animaci", "botón", "boton", "click", "login", "formulario", "lógica", "logica", "bug", "error")):
            targets.extend([r for r in existing if r.lower().endswith(".js")][:2])
            if any(w in q_low for w in ("html", "página", "pagina", "botón", "boton", "formulario", "login", "menú", "menu", "navbar", "interfaz")):
                targets.extend([r for r in existing if r.lower().endswith((".html", ".htm"))][:1])

        if any(w in q_low for w in ("api", "endpoint", "ruta", "backend", "servidor", "python", "función", "funcion", "clase")):
            targets.extend([r for r in existing if r.lower().endswith(".py")][:2])

        # 3. Respaldo: archivo activo en Monaco o entrypoint principal
        if not targets and active_file:
            norm_active = active_file.replace("\\", "/").lstrip("/")
            if norm_active in existing:
                targets.append(norm_active)
        if not targets:
            for preferred in ("app.js", "main.js", "index.html", "main.py", "app.py", "server.py"):
                match = next((r for r in existing if Path(r).name.lower() == preferred), None)
                if match:
                    targets.append(match)
                    if len(targets) >= 2:
                        break
        if not targets:
            targets = existing[:2]

        # Deduplicar preservando orden
        deduped: List[str] = []
        for t in targets:
            if t not in deduped:
                deduped.append(t)
        return deduped[:3]

    def _detect_python_symbol(self, user_query: str, file_path: Path) -> Optional[str]:
        """Si el usuario nombró una función o clase Python existente en el archivo, retorna su nombre exacto para `patch_symbol`."""
        if file_path.suffix.lower() != ".py" or not file_path.exists():
            return None
        src = file_path.read_text(encoding="utf-8", errors="replace")
        symbols = re.findall(r"(?m)^(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)\b", src)
        for sym in symbols:
            if re.search(rf"\b{re.escape(sym)}\b", user_query):
                return sym
        return None

    def iterate(
        self,
        user_query: str,
        workspace_dir: Path,
        active_file: Optional[str] = None,
        is_aborted=None,
    ) -> Generator[Dict[str, Any], None, None]:
        """Ejecuta una iteración incremental quirúrgica sin destruir archivos no relacionados."""
        ws_str = str(workspace_dir.resolve())
        aborted = is_aborted or (lambda: False)

        yield {
            "type": "step",
            "id": "iter_skeleton",
            "status": "running",
            "title": "[ITERATE 1/3] Analizando esqueleto del repositorio (MCP-AST)...",
            "detail": "Identificando archivos y símbolos objetivo sin regenerar el proyecto completo",
        }
        skel_res = call_mcp_json("mcp__mcp_ast__get_repo_skeleton", {"workspace_dir": ws_str, "max_files": 25})
        targets = self.select_target_files(user_query, workspace_dir, active_file=active_file)
        assets_list = skel_res.get("assets") or []
        assets_note = (
            "ASSETS REALES YA PRESENTES EN EL WORKSPACE (USA ESTAS RUTAS EXACTAS):\n"
            + "\n".join(f"- ./{a}" for a in assets_list[:12])
            if assets_list
            else ""
        )
        yield {
            "type": "step",
            "id": "iter_skeleton",
            "status": "done",
            "title": f"[ITERATE 1/3] Objetivo quirúrgico: {', '.join(targets) or 'ninguno'}",
            "detail": f"{len(targets)} archivo(s) seleccionado(s) | {len(assets_list)} asset(s) en disco",
        }

        if not targets:
            yield {"type": "done", "passed": False, "summary": "No se encontraron archivos de código existentes para iterar.", "files": [], "archetype": "iterate_app"}
            return

        sections = [
            ContextSection(name="Assets Locales", content=assets_note, priority=1, max_share=0.20),
            ContextSection(name="Esqueleto del Repo", content=skel_res.get("skeleton", ""), priority=2, max_share=0.55),
        ]
        compacted_ctx, _ = self.budget.pack(sections)

        modified_files: List[str] = []
        for idx, rel_path in enumerate(targets, 1):
            if aborted():
                break
            target_fp = (workspace_dir / rel_path).resolve()
            py_sym = self._detect_python_symbol(user_query, target_fp)

            if py_sym:
                yield {
                    "type": "step",
                    "id": f"iter_patch_{idx}",
                    "status": "running",
                    "title": f"[ITERATE 2/3] Parcheo AST quirúrgico: {rel_path}::{py_sym}...",
                    "detail": "Leyendo símbolo con read_symbol y aplicando patch_symbol",
                }
                read_res = call_mcp_json("mcp__mcp_ast__read_symbol", {"workspace_dir": ws_str, "file_path": rel_path, "symbol_name": py_sym})
                sym_src = read_res.get("source", "")
                f_spec = {
                    "path": rel_path,
                    "description": f"Parche quirúrgico del símbolo `{py_sym}` en {rel_path}",
                    "instructions": (
                        f"Modifica ÚNICAMENTE la función/clase `{py_sym}` para cumplir: {user_query}\n"
                        f"Devuelve la definición completa actualizada de `{py_sym}` sin el resto del archivo.\n"
                        f"Código actual de `{py_sym}`:\n{sym_src}"
                    ),
                }
                raw_sym, builder_label, _ = self.router.generate_module(f_spec, compacted_ctx, workspace_dir)
                clean_sym = extract_pure_code_from_llm(raw_sym, rel_path)
                p_res = call_mcp_json(
                    "mcp__mcp_ast__patch_symbol",
                    {"workspace_dir": ws_str, "file_path": rel_path, "symbol_name": py_sym, "new_source": clean_sym},
                )
                if p_res.get("success"):
                    full_updated = target_fp.read_text(encoding="utf-8", errors="replace")
                    modified_files.append(rel_path)
                    yield {
                        "type": "step",
                        "id": f"iter_patch_{idx}",
                        "status": "done",
                        "title": f"[ITERATE 2/3] `patch_symbol` aplicado a {rel_path}::{py_sym} ({builder_label})",
                        "detail": f"{p_res.get('lines_replaced', 0)} líneas actualizadas",
                    }
                    yield {"type": "file_code", "path": rel_path, "code": full_updated}
                    continue

            # Edición incremental de archivo completo preservando el resto del repo
            yield {
                "type": "step",
                "id": f"iter_patch_{idx}",
                "status": "running",
                "title": f"[ITERATE 2/3] Actualizando {rel_path}...",
                "detail": "Aplicando cambio incremental preservando contratos existentes",
            }
            f_spec = {
                "path": rel_path,
                "description": f"Actualización incremental de {rel_path}",
                "instructions": (
                    f"SOLICITUD DE CAMBIO INCREMENTAL DEL USUARIO:\n{user_query}\n\n"
                    f"Actualiza `{rel_path}` conservando toda la funcionalidad existente que no necesite cambiar. "
                    f"Si hay un asset 3D (.glb/.gltf) listado en los assets locales, cárgalo explícitamente con su ruta exacta."
                ),
            }
            raw_code, builder_label, _ = self.router.generate_module(f_spec, compacted_ctx, workspace_dir)
            pure_code, _, verify_note = verify_and_heal_code(
                raw_code or "",
                f_spec,
                self.cloud_cfg,
                local_port=self.local_port,
                workspace_dir=workspace_dir,
                sibling_signatures=compacted_ctx,
                use_cloud=self.use_cloud and not self.router.breaker.is_open(),
            )
            w_res = call_mcp_json("mcp__mcp_ast__write_module", {"workspace_dir": ws_str, "file_path": rel_path, "content": pure_code})
            if not w_res.get("success"):
                call_mcp_json("mcp__mcp_ast__write_module", {"workspace_dir": ws_str, "file_path": rel_path, "content": pure_code, "allow_placeholders": True})
            modified_files.append(rel_path)
            yield {
                "type": "step",
                "id": f"iter_patch_{idx}",
                "status": "done",
                "title": f"[ITERATE 2/3] {rel_path} actualizado ({builder_label})",
                "detail": verify_note,
            }
            yield {"type": "file_code", "path": rel_path, "code": pure_code}

        # QA determinista sobre el repo modificado
        yield {
            "type": "step",
            "id": "iter_qa",
            "status": "running",
            "title": "[ITERATE 3/3] QA Determinista post-parche (MCP-EXEC)...",
            "detail": "Verificando sintaxis, costuras DOM/API, eventos y bucle Three.js",
        }
        all_code_files = sorted(
            str(p.relative_to(workspace_dir)).replace("\\", "/")
            for p in workspace_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in self.CODE_EXTS and not any(part.startswith(".") for part in p.relative_to(workspace_dir).parts)
        )
        qa_res = call_mcp_json("mcp__mcp_exec__run_static_qa", {"workspace_dir": ws_str, "file_paths": all_code_files or modified_files})
        errs = qa_res.get("errors") or []

        if errs:
            culprit = errs[0].get("file") or modified_files[0]
            culprit_fp = (workspace_dir / culprit).resolve()
            if culprit_fp.exists():
                err_msg = "; ".join(f"[{e.get('gate')}] {e.get('error')}" for e in errs[:4])
                healed = _heal_code_with_local_qwen(
                    culprit_fp.read_text(encoding="utf-8", errors="replace"),
                    {"path": culprit, "description": culprit},
                    err_msg,
                    local_port=self.local_port,
                    sibling_signatures=compacted_ctx,
                )
                if healed and healed.strip():
                    clean_healed = extract_pure_code_from_llm(healed, culprit)
                    call_mcp_json("mcp__mcp_ast__write_module", {"workspace_dir": ws_str, "file_path": culprit, "content": clean_healed, "allow_placeholders": True})
                    yield {"type": "file_code", "path": culprit, "code": clean_healed, "healed": True}
                    qa_res = call_mcp_json("mcp__mcp_exec__run_static_qa", {"workspace_dir": ws_str, "file_paths": all_code_files or modified_files})
                    errs = qa_res.get("errors") or []

        passed = len(errs) == 0
        yield {
            "type": "step",
            "id": "iter_qa",
            "status": "done",
            "title": f"[ITERATE 3/3] QA Determinista: {'OK (0 errores)' if passed else f'{len(errs)} aviso(s)'}",
            "detail": ", ".join(qa_res.get("checks_run") or ["ok"]),
        }
        yield {
            "type": "done",
            "passed": passed,
            "summary": f"Iteración incremental completada sobre {', '.join(modified_files)}." if passed else f"Iterado con avisos: {errs[0].get('error', '')[:140]}",
            "files": modified_files,
            "archetype": "iterate_app",
        }
