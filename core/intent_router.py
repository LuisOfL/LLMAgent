# -*- coding: utf-8 -*-
"""
core/intent_router.py — Clasificador de Intención de 5 Vías para el Agente LLMAgent.

Clasifica cada prompt del usuario en exactamente 1 de 5 acciones:
  1. `create_app`       -> Creación de nueva app desde cero (`core/mcp_orchestrator.py` -> `run_elite_mcp_pipeline`)
  2. `iterate_app`      -> Edición incremental quirúrgica sobre repo existente (`patch_symbol` / `IncrementalIterator`)
  3. `download_asset`   -> Descarga directa verificada vía `MCP-ASSETS` / `download_url` (sin alucinaciones)
  4. `compile_run`      -> Compilación, QA determinista y ejecución en sandbox vía `MCP-EXEC`
  5. `answer_question`  -> Respuesta directa rápida del LLM (con RAG/Web si aplica)
"""

import re
from pathlib import Path
from typing import Dict, Optional

from core.local_codex_engine import classify_agent_intent


INTENT_LABELS: Dict[str, str] = {
    "create_app": "1) Crear Nueva Aplicación (Pipeline DAG 5-MCP)",
    "iterate_app": "2) Iterar / Parchear Aplicación Existente (Incremental AST)",
    "download_asset": "3) Descargar Archivo de Internet (MCP-ASSETS)",
    "compile_run": "4) Compilar / Verificar / Ejecutar (MCP-EXEC)",
    "answer_question": "5) Responder Pregunta (LLM Directo)",
}


def _workspace_has_code(workspace_dir: Optional[Path]) -> bool:
    if workspace_dir is None or not workspace_dir.exists():
        return False
    code_exts = {".py", ".js", ".html", ".htm", ".css", ".ts", ".tsx", ".jsx"}
    ignored = {".git", "__pycache__", "node_modules", ".venv", "venv", "engine", "models", ".antigravity_rag", ".audit"}
    for p in workspace_dir.iterdir():
        if p.name in ignored or p.name.startswith("."):
            continue
        if p.is_file() and p.suffix.lower() in code_exts:
            return True
    return False


def classify_user_intent(
    user_query: str,
    workspace_dir: Optional[Path] = None,
    active_file: Optional[str] = None,
) -> Dict[str, str]:
    """
    Devuelve {"intent": <clave>, "label": <descripción>, "reason": <motivo>}.
    Prioridades:
      1. Si pide crear/construir una app (incluso si además pide descargar un asset para esa app) -> `create_app`.
      2. Si pide descargar un archivo/URL/modelo/imagen/video/pdf sin pedir crear una app -> `download_asset`.
      3. Si pide compilar, verificar sintaxis, auditar o ejecutar el código -> `compile_run`.
      4. En cualquier otro caso (preguntas, explicaciones, dudas técnicas) -> `answer_question`.
    """
    q = (user_query or "").strip()
    q_low = q.lower()

    if not q:
        return {"intent": "answer_question", "label": INTENT_LABELS["answer_question"], "reason": "Consulta vacía"}

    # Señales de creación o modificación/integración en app / web / herramienta / script
    has_create_app = bool(re.search(
        r"\b(?:crea|creame|construye|desarrolla|dise[nñ]a|programa|genera|haz|hazme|hacer|pued(?:es)?\s+hacer)\b"
        r"[\w\s,]{0,45}"
        r"\b(?:app|aplicaci[oó]n|sistema|plataforma|proyecto|web|p[aá]gina|html|dashboard|api|servidor|herramienta|juego|calculadora|bot|gestor|cli|suite|motor|programa|visor|script)\b",
        q_low,
    )) or bool(re.search(
        r"\b(?:solo\s+(?:h?abra\s+)?un\s+html|un\s+solo\s+html|repara\s+todo|completa\s+el\s+proyecto|refactoriza\s+el\s+proyecto)\b",
        q_low,
    ))

    # Señales de edición / integración sobre la app actual (ej. "agregalo como modelo 3d en la pagina", "ponlo en el html", "cambia el fondo")
    has_modify_app = bool(re.search(
        r"\b(?:agrega(?:lo|la|le|me)?|agr[eé]ga(?:lo|la|le|me)?|a[nñ]ade(?:lo|la|le)?|a[nñ][aá]de(?:lo|la|le)?|"
        r"pon(?:lo|la|le)?|integra(?:lo|la)?|int[eé]gra(?:lo|la)?|incluye(?:lo|la)?|incl[uú]ye(?:lo|la)?|"
        r"mete(?:lo|la)?|m[eé]te(?:lo|la)?|carga(?:lo|la)?|c[aá]rga(?:lo|la)?|inserta(?:lo|la)?|ins[eé]rta(?:lo|la)?|"
        r"modifica|actualiza|cambia|edita|arregla|corrige|mejora|muestra(?:lo|la)?|renderiza(?:lo|la)?|"
        r"add|integrate|insert|update|modify|fix)\b"
        r"[\w\s,./\-]{0,55}"
        r"\b(?:p[aá]gina|pagina|web|html|app|aplicaci[oó]n|juego|proyecto|modelo\s*3d|3d|escena|canvas|visor|"
        r"c[oó]digo|archivo|script|app\.js|index\.html|style\.css|interfaz|bot[oó]n|botones|men[uú]|navbar|fondo|color|estilo|luz|luces|c[aá]mara|glb|gltf)\b",
        q_low,
    ))

    # Señales de descarga directa de archivo de internet
    has_download_verb = bool(re.search(r"\b(?:descarga|descargar|descargue|baja|bajar|download|trae\s+de\s+internet)\b", q_low))
    has_url = bool(re.search(r"https?://", q_low))

    # Señales de compilación / verificación / ejecución
    has_compile_verb = bool(re.search(
        r"\b(?:compila|compilar|compilaci[oó]n|verifica\s+(?:la\s+)?sintaxis|revisa\s+errores|checa\s+sintaxis|valida\s+el\s+proyecto|ejecuta\s+las\s+pruebas|corre\s+pytest|lint|check\s+syntax)\b",
        q_low,
    ))
    has_run_verb = bool(re.search(
        r"^(?:ejecuta|corre|lanza|prueba|inicia|arranca)\b",
        q_low,
    ))

    # Señales puras de pregunta
    is_pure_question = bool(re.search(
        r"^(?:qu[eé]\s+(?:es|son|hace|significa)|c[oó]mo\s+(?:funciona|se\s+usa|puedo|hago)|por\s+qu[eé]|cu[aá]l\s+es|expl[ií]ca(?:me)?|dime|resume|diferencia\s+entre)\b",
        q_low,
    ))

    # 1. Edición incremental (`iterate_app`) sobre repo existente (ej. "cambia el color", "añade login", "agregalo como modelo 3d en la pagina", "arregla el bug")
    if has_modify_app and not has_create_app and not is_pure_question:
        if _workspace_has_code(workspace_dir) or active_file:
            return {
                "intent": "iterate_app",
                "label": INTENT_LABELS["iterate_app"],
                "reason": "Edición incremental sobre repositorio existente (sin regenerar desde cero)",
            }
        return {
            "intent": "create_app",
            "label": INTENT_LABELS["create_app"],
            "reason": "Solicitud de construcción/integración en workspace vacío",
        }

    # 2. Crear nueva app desde cero (`create_app`)
    if has_create_app and not is_pure_question:
        return {
            "intent": "create_app",
            "label": INTENT_LABELS["create_app"],
            "reason": "Solicitud de creación de nueva aplicación o web",
        }

    # 2. Descargar algo de internet
    if has_download_verb or (has_url and any(w in q_low for w in ("ponlo", "ponla", "guarda", "repo", "carpeta", "archivo", "modelo"))):
        return {
            "intent": "download_asset",
            "label": INTENT_LABELS["download_asset"],
            "reason": "Solicitud de descarga de archivo externo al repositorio",
        }

    # 3. Compilar / verificar / ejecutar
    if (has_compile_verb or has_run_verb) and not is_pure_question:
        return {
            "intent": "compile_run",
            "label": INTENT_LABELS["compile_run"],
            "reason": "Solicitud de compilación, QA estático o ejecución",
        }

    # 4. Pregunta pura
    if is_pure_question:
        return {
            "intent": "answer_question",
            "label": INTENT_LABELS["answer_question"],
            "reason": "Pregunta o solicitud explicativa",
        }

    # Respaldo con el clasificador contextual existente
    if workspace_dir is not None:
        fallback = classify_agent_intent(q, workspace_dir, active_file=active_file)
        if fallback == "build_app":
            return {
                "intent": "create_app",
                "label": INTENT_LABELS["create_app"],
                "reason": "Clasificado como construcción de proyecto por contexto del workspace",
            }

    return {
        "intent": "answer_question",
        "label": INTENT_LABELS["answer_question"],
        "reason": "Consulta general / conversación técnica",
    }
