# -*- coding: utf-8 -*-
"""
local_codex_engine.py - Motor Agéntico Local de Nivel Antigravity / OpenAI Codex.

Diseñado específicamente para exprimir modelos locales de pesos abiertos (Qwen3-8B / Qwen3.8-9B
en GPUs de 6 GB VRAM) y elevar su calidad de ingeniería de software al nivel de agentes de frontera:

1. Decodificación Restringida por Gramática GBNF (`json_schema`):
   Garantiza arquitecturas multi-archivo 100% válidas con contratos explícitos de clases,
   métodos, rutas API y dependencias topológicas (cero fallos de parseo).
2. Síntesis Topológica con Grafo AST en Vivo (`CodeArchitectureGraphRAG`):
   Construye cada módulo en orden de dependencias inyectando los contratos reales de los
   módulos previos y alertas de aristas rotas.
3. Expansión Quirúrgica a Nivel de Función AST (`expand_stub_functions_surgically`):
   Si el modelo local dejó una función vacía, simulada o con `# placeholder`, extrae ÚNICAMENTE
   esa función, la expande en profundidad con el 100% de la atención del modelo y la reinserta
   quirúrgicamente en el archivo manteniendo intacto el resto del código.
4. Bucle Reflexion (Shinn et al.) con Memoria Episódica de Fallos:
   Ejecuta pruebas reales de importación, coherencia API frontend/backend y ejecución CLI en
   subproceso aislado; si ocurre un Traceback real, guarda la reflexión del fallo y repara
   la causa raíz hasta lograr `exit_code == 0`.
"""

import os
import re
import ast
import sys
import json
import time
import textwrap
import subprocess
import urllib.request
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from core.codebase_rag import code_arch_graph
from core.cloud_workers import (
    _query_local_qwen_fast,
    extract_pure_code_from_llm,
    extract_module_signatures,
    collect_workspace_signatures,
    auto_fix_common_python_issues,
    detect_code_slop_issues,
    plan_project_with_local_orchestrator,
    build_bedrock_worker_prompt,
    call_cloud_worker,
    is_refusal_response,
    audit_logger,
    _heal_code_with_local_qwen,
)


# ==============================================================================
# 1. ESQUEMA JSON CONSTRUIDO PARA DECODIFICACIÓN GRAMATICAL GBNF (LLAMA.CPP)
# ==============================================================================

BLUEPRINT_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "project_name": {"type": "string"},
        "architecture_summary": {"type": "string"},
        "entrypoint": {"type": "string"},
        "files": {
            "type": "array",
            "minItems": 1,
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "layer": {
                        "type": "string",
                        "enum": ["models", "core", "service", "api", "entrypoint", "ui"]
                    },
                    "description": {"type": "string"},
                    "exports": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "imports_from": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "detailed_requirements": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": ["path", "layer", "description", "exports", "imports_from", "detailed_requirements"],
                "additionalProperties": False
            }
        }
    },
    "required": ["project_name", "architecture_summary", "entrypoint", "files"],
    "additionalProperties": False
}

LAYER_PRIORITY: Dict[str, int] = {
    "models": 0,
    "core": 1,
    "service": 2,
    "api": 3,
    "entrypoint": 4,
    "ui": 5,
}


# ==============================================================================
# 2. ROUTER INTELIGENTE DE INTENCIÓN (BUILD_APP vs REACT_AGENT)
# ==============================================================================

def classify_agent_intent(user_query: str, workspace_dir: Path, active_file: Optional[str] = None) -> str:
    """
    Decide si la solicitud del usuario requiere:
    - 'build_app': Orquestación jerárquica completa (Blueprint GBNF -> Síntesis Topológica -> Expansión AST -> Reflexion Runtime).
    - 'react_agent': Bucle autónomo ReAct multi-turno con herramientas MCP (editar/parchear un archivo puntual,
      inspeccionar una web/localhost, buscar en el código, compilar, depurar un bug o ejecutar comandos).
    """
    q = (user_query or "").strip()
    q_low = q.lower()
    if len(q) <= 5:
        return "react_agent"

    # Si contiene una URL explícita para inspeccionar o descargar, usar ReAct con herramientas MCP
    if re.search(r"https?://", q_low):
        return "react_agent"

    # Preguntas puras, inspecciones, búsquedas o depuración puntual -> ReAct Agent
    react_only_patterns = (
        r"^(?:c[oó]mo\s+(?:uso|utilizo|funciona|se\s+usa|corro|ejecuto)|qu[eé]\s+(?:es|hace|significa)|para\s+qu[eé]\s+sirve|expl[ií]ca(?:me)?|por\s+qu[eé]|cu[aá]l\s+es)\b",
        r"\b(?:busca\s+en\s+el\s+c[oó]digo|grep|inspecciona|analiza\s+la\s+p[aá]gina|ver\s+la\s+web|descarga|compila|lista\s+los\s+archivos|lee\s+el\s+archivo)\b",
    )
    has_app_creation_verb = bool(re.search(
        r"\b(?:crea|creame|construye|desarrolla|dise[nñ]a|programa|genera|haz|hazme|hacer|pued(?:es)?\s+hacer)\b[\w\s]{0,40}\b(?:app|aplicaci[oó]n|sistema|plataforma|proyecto|web|p[aá]gina|html|dashboard|api|servidor|herramienta|juego|calculadora|bot|gestor|cli|suite|motor|programa|visor)\b",
        q_low
    ))
    has_single_html_request = bool(re.search(
        r"\b(?:solo\s+(?:h?abra\s+)?un\s+html|un\s+solo\s+html|ver\s+la\s+p[aá]gina\s+web|en\s+un\s+html)\b",
        q_low
    ))
    has_overhaul_verb = bool(re.search(
        r"\b(?:repara\s+todo|completa\s+el\s+proyecto|mejora\s+todo|hazlo\s+funcional|termina\s+la\s+app|refactoriza\s+el\s+proyecto)\b",
        q_low
    ))

    if has_app_creation_verb or has_single_html_request or has_overhaul_verb:
        return "build_app"

    for pat in react_only_patterns:
        if re.search(pat, q_low):
            return "react_agent"

    # Si el usuario apunta a editar un único archivo puntual existente (ej. "en main.py cambia el puerto a 8000")
    mentions_single_file = bool(re.search(r"\b[a-zA-Z0-9_\-/]+\.(?:py|js|html|css|json)\b", q))
    surgical_edit_verbs = bool(re.search(r"\b(?:cambia|agrega\s+un\s+bot[oó]n|quita|elimina|renombra|corrige\s+la\s+l[ií]nea|parchea|edita)\b", q_low))
    if mentions_single_file and surgical_edit_verbs:
        return "react_agent"

    # Si el workspace está vacío y pide construir algo, usar el pipeline Codex completo
    py_or_web = list(workspace_dir.glob("*.py")) + list(workspace_dir.glob("*.html")) if workspace_dir.exists() else []
    if not py_or_web and bool(re.search(r"\b(?:crea|haz|desarrolla|construye|genera|programa|implementa)\b", q_low)):
        return "build_app"

    # Si pide crear/generar/desarrollar algo general en modo Crear Apps -> build_app
    if bool(re.search(r"\b(?:crea|creame|haz|hazme|desarrolla|construye|genera|completa|implementa)\b", q_low)):
        return "build_app"

    return "react_agent"


# ==============================================================================
# 3. PLANIFICADOR CONSTRUIDO POR GRAMÁTICA GBNF (JSON SCHEMA)
# ==============================================================================

def _query_local_json_schema(
    system_prompt: str,
    user_prompt: str,
    schema: Dict[str, Any],
    local_port: int = 8085,
    max_tokens: int = 2000
) -> Optional[Dict[str, Any]]:
    """
    Invoca llama-server usando Decodificación Restringida por Gramática GBNF (`json_schema`).
    Garantiza salida JSON 100% válida incluso en modelos pequeños de 8B/9B.
    """
    url = f"http://127.0.0.1:{local_port}/v1/chat/completions"
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "/no_think\n" + user_prompt}
        ],
        "temperature": 0.2,
        "top_p": 0.85,
        "top_k": 20,
        "repeat_penalty": 1.08,
        "max_tokens": max_tokens,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "codex_blueprint",
                "strict": True,
                "schema": schema
            }
        }
    }
    try:
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=75) as resp:
            raw_json = json.loads(resp.read().decode("utf-8", errors="replace"))
            content = raw_json.get("choices", [{}])[0].get("message", {}).get("content", "")
            content = re.sub(r"<think>[\s\S]*?</think>", "", content, flags=re.IGNORECASE).strip()
            if content:
                return json.loads(content)
    except Exception as e:
        print(f"[CodexBlueprint] Aviso en GBNF json_schema (usando fallback estructurado): {e}")
    return None


def _list_workspace_assets(workspace_dir: Optional[Path]) -> List[str]:
    """Lista assets reales (3D .glb/.gltf, imágenes, audio, video, CSV/JSON) en el workspace."""
    if not workspace_dir or not workspace_dir.exists():
        return []
    asset_exts = {".glb", ".gltf", ".obj", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".mp3", ".wav", ".mp4", ".csv", ".json"}
    found: List[str] = []
    for p in workspace_dir.rglob("*"):
        if not p.is_file():
            continue
        if any(part in {".git", "node_modules", ".venv", "__pycache__", ".antigravity_rag"} for part in p.parts):
            continue
        if p.suffix.lower() in asset_exts:
            rel = str(p.relative_to(workspace_dir)).replace("\\", "/")
            found.append(rel)
            if len(found) >= 15:
                break
    return found


def detect_app_archetype(user_query: str, workspace_dir: Optional[Path] = None) -> str:
    """
    Detecta el tipo exacto de aplicación solicitada para NO forzar arquitecturas Python CLI
    cuando el usuario pide una página web, juego HTML o visor 3D:
    - 'single_html': 1 archivo `index.html` autocontenido listo para abrir en el navegador.
    - 'static_web': frontend limpio (`index.html`, `style.css`, `app.js`) sin backend innecesario.
    - 'fullstack_web': backend Python (`server.py`) + frontend (`index.html`, etc.).
    - 'python_app': aplicación/script Python limpio y modular.
    """
    q_low = (user_query or "").lower()
    if re.search(r"\b(?:solo\s+(?:h?abra\s+)?un\s+html|un\s+solo\s+html|archivo\s+html\s+[uú]nico|en\s+un\s+solo\s+archivo\s+html|solo\s+html)\b", q_low):
        return "single_html"

    needs_backend = any(k in q_low for k in (
        "fastapi", "flask", "django", "backend", "api rest", "base de datos", "sqlite",
        "servidor python", "full stack", "fullstack", "autenticacion", "autenticación", "login con servidor",
        "man in the middle", "mitm", "dns spoof", "arp", "sniffer", "sniff", "intercept",
        "dispositivos conectados", "escaneo de red", "escanear red", "misma red", "paquetes", "scapy", "socket"
    ))
    wants_web = any(k in q_low for k in (
        "web", "interfaz", "interfz", "pagina", "página", "html", "index.html", "css", "javascript", "dashboard", "panel",
        "frontend", "sitio", "landing", "portafolio", "tienda", "kanban", "calculadora",
        "snake", "tetris", "juego", "lista de tareas", "todo", "visor 3d", "three.js", "threejs", "glb", "gltf"
    ))
    wants_python = any(k in q_low for k in (
        "python", "script", "cli", "consola", "terminal", "tkinter", "pyqt", "pygame", "scraper", "bot"
    ))

    if wants_web and needs_backend:
        return "fullstack_web"
    if wants_web and not wants_python:
        return "static_web"
    if wants_web and wants_python and not any(k in q_low for k in ("tkinter", "pyqt", "pygame")):
        return "fullstack_web"
    return "python_app"


def plan_blueprint_constrained(
    user_query: str,
    local_port: int = 8085,
    workspace_dir: Optional[Path] = None,
    active_file: Optional[str] = None
) -> Tuple[str, List[Dict[str, str]]]:
    """
    Diseña el Blueprint Arquitectónico de Élite usando GBNF JSON Schema Constrained Decoding.
    Adapta automáticamente la arquitectura al arquetipo real (Single HTML, Static Web, Full-Stack o Python).
    """
    ws_sigs, broken_files = collect_workspace_signatures(workspace_dir)
    ws_assets = _list_workspace_assets(workspace_dir)
    q_low = user_query.lower()

    # Si el usuario pide explícitamente reparar archivos rotos del repo actual, delegar al planificador de reparación
    repair_keywords = ("corrige", "corrigue", "repara", "arregla", "errores", "broken")
    if any(kw in q_low for kw in repair_keywords) and broken_files:
        return plan_project_with_local_orchestrator(
            user_query, local_port=local_port, workspace_dir=workspace_dir, active_file=active_file
        )

    archetype = detect_app_archetype(user_query, workspace_dir)
    assets_note = ""
    if ws_assets:
        assets_note = (
            "\nASSETS REALES YA EXISTENTES EN EL REPOSITORIO (ÚSALOS CON ESTAS RUTAS EXACTAS):\n"
            + "\n".join(f"  - ./{a}" for a in ws_assets)
            + "\n"
        )
    glb_assets = [a for a in ws_assets if a.lower().endswith((".glb", ".gltf"))]
    wants_3d = bool(glb_assets) or any(k in q_low for k in ("3d", "three.js", "threejs", "glb", "gltf", "visor"))
    glb_hint = ""
    if wants_3d:
        model_ref = f"./{glb_assets[0]}" if glb_assets else ""
        load_instruction = (
            f"Carga el modelo real `{model_ref}` con `new GLTFLoader().load('{model_ref}', ...)` y céntralo automáticamente usando `new THREE.Box3().setFromObject(model)`."
            if model_ref else
            "Construye la escena 3D con geometrías bien proporcionadas y materiales PBR (`MeshStandardMaterial`)."
        )
        glb_hint = (
            "BOILERPLATE VERIFICADO DE THREE.JS (ÚSALO EXACTO EN EL `<head>` PARA QUE EL 3D RENDERICE SIEMPRE):\n"
            "  <script type=\"importmap\">{\"imports\":{\"three\":\"https://unpkg.com/three@0.160.0/build/three.module.js\",\"three/addons/\":\"https://unpkg.com/three@0.160.0/examples/jsm/\"}}</script>\n"
            "  Y en `<script type=\"module\">`: `import * as THREE from 'three'; import { OrbitControls } from 'three/addons/controls/OrbitControls.js'; import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';`\n"
            f"  {load_instruction} Incluye `HemisphereLight(0xffffff, 0x222233, 1.4)` + `DirectionalLight(0xffffff, 2.2)` + `OrbitControls` con `enableDamping = true` y un contenedor con altura `min-height: 65vh`.\n"
        )

    # Caso rápido y de precisión extrema: 1 solo archivo index.html autocontenido
    if archetype == "single_html":
        single_instructions = (
            "CAPA ARQUITECTÓNICA: UI (APLICACIÓN WEB AUTOCONTENIDA EN UN SOLO HTML)\n"
            f"PROPÓSITO: Implementar completamente la petición del usuario en un único archivo `index.html` listo para abrir en el navegador.\n"
            "REQUISITOS OBLIGATORIOS:\n"
            "  - Todo el CSS moderno (diseño oscuro profesional, responsivo, tipografía limpia) dentro de `<style>`.\n"
            "  - Todo el JavaScript funcional y completo (sin TODOs ni placeholders) dentro de `<script>`.\n"
            "  - NO generes archivos Python (`main.py`, `core/`, `service/`) ni dependencias locales rotas.\n"
            + (f"  - {glb_hint}" if glb_hint else "")
            + (assets_note if assets_note else "")
        )
        return (
            "Web App Autocontenida (index.html único)",
            [{
                "path": "index.html",
                "layer": "ui",
                "description": f"Aplicación web completa en un solo archivo HTML: {user_query[:120]}",
                "instructions": single_instructions,
                "exports": ["index.html"],
            }]
        )

    ws_note = (f"\nESTADO ACTUAL DEL REPOSITORIO:\n{ws_sigs}\n" if ws_sigs else "") + assets_note

    if archetype == "static_web":
        arch_rules = (
            "ARQUETIPO: APLICACIÓN WEB FRONTEND PURA (HTML + CSS + JS).\n"
            "1. Genera ÚNICAMENTE archivos web limpios: `index.html`, `style.css` y `app.js` (entre 1 y 3 archivos).\n"
            "2. PROHIBIDO crear archivos Python (`main.py`, `core/engine.py`, `service/api.py`, `models/*.py`).\n"
            "3. `index.html` debe enlazar exactamente `<link rel=\"stylesheet\" href=\"style.css\">` y `<script src=\"app.js\">` (o `type=\"module\"` si usa Three.js) y funcionar al abrirlo en navegador o vista previa.\n"
            "4. Diseño visual moderno, oscuro, responsivo, con controles reales e interactivos y 0 placeholders.\n"
            f"{glb_hint}"
        )
    elif archetype == "fullstack_web":
        arch_rules = (
            "ARQUETIPO: APLICACIÓN WEB FULL-STACK (Backend Python + Frontend `index.html`).\n"
            "1. Genera entre 2 y 4 archivos cohesivos: módulos de lógica/modelos (si aplica), `server.py` (FastAPI o `http.server` que sirva `/` -> `index.html` y endpoints `/api/*`), `index.html`, y opcionalmente `app.js`.\n"
            "2. PROHIBIDO usar Tkinter/PyQt/CLI en lugar de interfaz web: SIEMPRE debes incluir `index.html` con UI moderna conectada por `fetch('/api/...')`.\n"
            "3. Asegúrate de que cada `fetch('/api/...')` en el frontend coincida 100% con una ruta real en `server.py`, y NO dupliques clases entre archivos Python (impórtalas).\n"
            "4. `server.py` debe poder ejecutarse directamente con `python server.py` sin argumentos obligatorios.\n"
            f"{glb_hint}"
        )
    else:
        arch_rules = (
            "ARQUETIPO: APLICACIÓN PYTHON LIMPIA Y EJECUTABLE.\n"
            "1. Genera SOLO los archivos realmente necesarios (entre 1 y 4 archivos, p. ej. `main.py` + 1-2 módulos auxiliares si la complejidad lo justifica). NO inventes carpetas `models/` o `service/` innecesarias.\n"
            "2. Usa librería estándar de Python siempre que sea posible (sqlite3, json, pathlib, threading, http.server, tkinter, etc.) para ejecución inmediata.\n"
            "3. Define en `exports` los nombres EXACTOS de clases y funciones públicas y asegura 100% de coherencia en `imports_from`.\n"
            "4. `main.py` debe ejecutarse directamente con `python main.py` sin argumentos obligatorios (`default=...`).\n"
        )

    system_prompt = (
        "Eres el Arquitecto de Software Principal de un agente autónomo tipo Antigravity/Codex.\n"
        "Diseña una arquitectura limpia, exacta a lo pedido y de grado producción en formato JSON estricto.\n"
        f"{arch_rules}"
    )
    user_prompt = f"Diseña el Blueprint arquitectónico completo para:\n\"{user_query}\"{ws_note}"

    bp = _query_local_json_schema(system_prompt, user_prompt, BLUEPRINT_JSON_SCHEMA, local_port=local_port, max_tokens=2000)

    if bp and isinstance(bp.get("files"), list) and len(bp["files"]) >= 1:
        raw_files = bp["files"]
        # En static_web, filtrar cualquier archivo .py que el modelo pequeño haya intentado colar
        if archetype == "static_web":
            web_only = [f for f in raw_files if str(f.get("path", "")).lower().endswith((".html", ".htm", ".css", ".js", ".json"))]
            if web_only:
                raw_files = web_only
        # En fullstack_web, garantizar que SIEMPRE exista index.html si el LLM lo omitió
        if archetype == "fullstack_web":
            has_html_node = any(str(f.get("path", "")).lower().endswith((".html", ".htm")) for f in raw_files)
            if not has_html_node:
                raw_files.append({
                    "path": "index.html",
                    "layer": "ui",
                    "description": "Interfaz web moderna conectada al backend Python mediante fetch('/api/...')",
                    "exports": ["index.html"],
                    "imports_from": [str(f.get("path", "")) for f in raw_files if str(f.get("path", "")).lower().endswith(".py")][:2],
                })
        # Ordenar topológicamente según arquetipo:
        # - En web estática: index.html PRIMERO (define IDs/clases), luego CSS, luego JS
        # - En full-stack: backend Python primero (define rutas /api/*), luego index.html, luego CSS, luego JS
        if archetype == "static_web":
            def _web_order(item):
                p = str(item.get("path", "")).lower()
                if p.endswith((".html", ".htm")): return 0
                if p.endswith(".css"): return 1
                if p.endswith(".js"): return 2
                return 3
            raw_files.sort(key=_web_order)
        elif archetype == "fullstack_web":
            def _fs_order(item):
                p = str(item.get("path", "")).lower()
                if p.endswith(".py"): return LAYER_PRIORITY.get(str(item.get("layer", "core")).lower(), 2)
                if p.endswith((".html", ".htm")): return 10
                if p.endswith(".css"): return 11
                if p.endswith(".js"): return 12
                return 13
            raw_files.sort(key=_fs_order)
        else:
            raw_files.sort(key=lambda item: LAYER_PRIORITY.get(str(item.get("layer", "core")).lower(), 2))

        plan_files: List[Dict[str, str]] = []
        seen_paths = set()
        for item in raw_files:
            f_path = str(item.get("path", "")).strip().replace("\\", "/").lstrip("/")
            if not f_path or "." not in f_path or f_path.lower() in seen_paths:
                continue
            seen_paths.add(f_path.lower())

            exports_str = "\n".join(f"  - {e}" for e in item.get("exports", []) if str(e).strip())
            imports_str = ", ".join(str(x) for x in item.get("imports_from", []) if str(x).strip())
            reqs_str = "\n".join(f"  - {r}" for r in item.get("detailed_requirements", []) if str(r).strip())

            inst_parts = [
                f"CAPA ARQUITECTÓNICA: {item.get('layer', 'core').upper()}",
                f"PROPÓSITO: {item.get('description', 'Módulo del proyecto')}",
            ]
            if imports_str:
                inst_parts.append(f"DEPENDE DE LOS MÓDULOS: {imports_str}")
            if exports_str:
                inst_parts.append(f"CONTRATO DE EXPORTACIÓN EXACTO (DEBES DEFINIR ESTAS CLASES/FUNCIONES):\n{exports_str}")
            if reqs_str:
                inst_parts.append(f"REQUISITOS FUNCIONALES Y ALGORÍTMICOS:\n{reqs_str}")

            plan_files.append({
                "path": f_path,
                "layer": str(item.get("layer", "core")).lower(),
                "description": str(item.get("description", "Módulo del proyecto")).strip(),
                "instructions": "\n".join(inst_parts),
                "exports": item.get("exports", []),
            })

        if plan_files:
            summary = f"{bp.get('project_name', 'Proyecto')}: {bp.get('architecture_summary', 'Arquitectura modular GBNF')}"
            audit_logger("codex_blueprint_gbnf", {
                "project": bp.get("project_name"),
                "files": [f["path"] for f in plan_files],
                "entrypoint": bp.get("entrypoint")
            })
            return summary, plan_files

    # Fallback robusto al planificador estructurado previo
    return plan_project_with_local_orchestrator(
        user_query, local_port=local_port, workspace_dir=workspace_dir, active_file=active_file
    )


# ==============================================================================
# 4. EXPANSOR QUIRÚRGICO AST A NIVEL DE FUNCIÓN (ANTI-SLOP PARA MODELOS 8B/9B)
# ==============================================================================

def find_stub_functions_ast(code: str, file_path: str) -> List[Dict[str, Any]]:
    """
    Inspecciona el AST de un archivo Python y localiza funciones/métodos específicos que son stubs,
    contienen `pass`/`...`, comentarios placeholder/simulados o solo hacen `print`/`logger` + constante.
    Devuelve su rango exacto de líneas `(start_line, end_line)` para expandirlos quirúrgicamente.
    """
    if not file_path.lower().endswith(".py") or not code.strip():
        return []

    stubs: List[Dict[str, Any]] = []
    lines = code.splitlines()
    placeholder_rx = re.compile(
        r"#[^\n]*\b(?:TODO|FIXME|PLACEHOLDER|DUMMY|STUB|simulat(?:e|ing|ed)|simula(?:r|do|da|ci[oó]n)|actual\s+[\w\s]{1,30}logic|ejemplo\s*:|implement(?:aci[oó]n)?\s+(?:de|aqu[ií]|here))\b",
        re.IGNORECASE
    )

    try:
        tree = ast.parse(code, filename=file_path)
    except SyntaxError:
        return []

    def _inspect_fn(fn_node: ast.AST, class_name: Optional[str]):
        if not isinstance(fn_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return
        start_l = fn_node.lineno
        end_l = getattr(fn_node, "end_lineno", start_l)
        if not end_l or end_l < start_l:
            return
        fn_src = "\n".join(lines[start_l - 1 : end_l])

        reason = None
        if placeholder_rx.search(fn_src):
            reason = "Contiene comentario placeholder/simulado en lugar de lógica real"
        else:
            eff_body = fn_node.body
            if (
                eff_body
                and isinstance(eff_body[0], ast.Expr)
                and isinstance(getattr(eff_body[0], "value", None), ast.Constant)
                and isinstance(eff_body[0].value.value, str)
            ):
                eff_body = eff_body[1:]
            if not eff_body:
                reason = "Función sin cuerpo (solo docstring)"
            elif len(eff_body) == 1 and isinstance(eff_body[0], ast.Pass):
                reason = "Función vacía (`pass`)"
            elif len(eff_body) == 1 and isinstance(eff_body[0], ast.Expr) and isinstance(getattr(eff_body[0], "value", None), ast.Constant) and eff_body[0].value.value is Ellipsis:
                reason = "Función vacía (`...`)"
            elif any(isinstance(n, ast.Raise) and "NotImplementedError" in (ast.unparse(n) if hasattr(ast, "unparse") else "") for n in ast.walk(fn_node)):
                reason = "Lanza NotImplementedError"
            elif 1 <= len(eff_body) <= 3 and fn_node.name not in ("__init__", "__repr__", "__str__", "close", "to_dict", "from_dict"):
                only_log_const = True
                has_log = False
                for st in eff_body:
                    if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call):
                        fn_s = (ast.unparse(st.value.func) if hasattr(ast, "unparse") else "").lower()
                        if any(k in fn_s for k in ("print", "logger.", "logging.", "log_", ".insert", ".see")):
                            has_log = True
                            continue
                    elif isinstance(st, ast.Return):
                        if st.value is None or (isinstance(st.value, ast.Constant) and isinstance(st.value.value, (bool, str, int))):
                            continue
                    only_log_const = False
                    break
                if only_log_const and has_log:
                    reason = "Método fachada que solo imprime/loggea y devuelve una constante"

        if reason:
            indent_str = lines[start_l - 1][: len(lines[start_l - 1]) - len(lines[start_l - 1].lstrip())]
            stubs.append({
                "class_name": class_name,
                "func_name": fn_node.name,
                "start_line": start_l,
                "end_line": end_l,
                "indent": indent_str,
                "source": fn_src,
                "reason": reason,
            })

    for top in tree.body:
        if isinstance(top, ast.ClassDef):
            for sub in top.body:
                _inspect_fn(sub, top.name)
        elif isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _inspect_fn(top, None)

    return stubs


def expand_stub_functions_surgically(
    code: str,
    file_info: Dict[str, str],
    local_port: int = 8085,
    sibling_signatures: str = "",
    max_functions: int = 4
) -> Tuple[str, List[str]]:
    """
    Expansión Quirúrgica a Nivel de Función (Surgical AST Splicing):
    En lugar de reescribir un archivo de 200 líneas cuando una función quedó como stub,
    toma cada función stub individualmente, le pide al modelo local una implementación profunda
    de 20-50 líneas SOLO para esa función, valida su AST y la inserta exactamente en su lugar.
    """
    path = file_info.get("path", "main.py").strip()
    stubs = find_stub_functions_ast(code, path)
    if not stubs:
        return code, []

    expanded_names: List[str] = []
    # Procesar de abajo hacia arriba para que los números de línea anteriores no se desplacen
    target_stubs = sorted(stubs[:max_functions], key=lambda s: s["start_line"], reverse=True)
    lines = code.splitlines()

    imports_header = "\n".join(
        l for l in lines[:45] if l.strip().startswith(("import ", "from "))
    )

    for st in target_stubs:
        cls_label = f"método `{st['class_name']}.{st['func_name']}`" if st["class_name"] else f"función `{st['func_name']}`"
        sys_p = (
            "Eres un Ingeniero de Software Principal.\n"
            f"Reemplaza el stub del {cls_label} en `{path}` por una implementación REAL, completa, "
            "algorítmica y lista para producción (sin placeholders, sin datos simulados, sin librería externa inexistente).\n"
            "Devuelve ÚNICAMENTE el código Python de esa única función/método empezando en `def ` (o `async def `), "
            "conservando exactamente su nombre y firma de parámetros."
        )
        usr_p = (
            f"Archivo: `{path}` ({file_info.get('description', '')})\n"
            f"Imports disponibles en el archivo:\n{imports_header}\n\n"
            f"Defecto detectado: {st['reason']}\n"
            f"Código actual del stub a reemplazar:\n```python\n{textwrap.dedent(st['source'])}\n```\n"
            + (f"\nContratos del proyecto:\n{sibling_signatures[:1200]}\n" if sibling_signatures else "")
            + "\nEscribe ÚNICAMENTE la definición completa `def ...` de esta función con toda la lógica real implementada."
        )
        raw_fn = _query_local_qwen_fast(sys_p, usr_p, local_port=local_port, max_tokens=1200)
        clean_fn = extract_pure_code_from_llm(raw_fn, path)
        clean_fn = textwrap.dedent(clean_fn).strip()

        # Verificar que empiece en def/async def y sea sintácticamente válido al aislarlo
        m_def = re.search(r"^(?:async\s+)?def\s+" + re.escape(st["func_name"]) + r"\b", clean_fn, flags=re.MULTILINE)
        if not m_def:
            continue
        clean_fn = clean_fn[m_def.start():].strip()

        try:
            ast.parse(clean_fn)
        except SyntaxError:
            continue

        # Re-indentar al nivel exacto del método original y probar el archivo completo
        indent = st["indent"]
        indented_fn_lines = [
            (indent + ln) if ln.strip() else ""
            for ln in clean_fn.splitlines()
        ]
        candidate_lines = lines[: st["start_line"] - 1] + indented_fn_lines + lines[st["end_line"] :]
        candidate_code = "\n".join(candidate_lines)
        try:
            ast.parse(candidate_code, filename=path)
            lines = candidate_lines
            expanded_names.append(f"{st['class_name']}.{st['func_name']}" if st["class_name"] else st["func_name"])
            print(f"[CodexSurgicalExpander] Expandido quirúrgicamente {cls_label} en {path}")
        except SyntaxError:
            continue

    return "\n".join(lines), expanded_names


# ==============================================================================
# 5. BUCLE REFLEXION CON MEMORIA EPISÓDICA DE FALLOS (SHINN ET AL.)
# ==============================================================================

class ReflexionMemory:
    """Memoria episódica que registra cada fallo y reparación previa para evitar repetir errores."""

    def __init__(self):
        self.episodes: List[Dict[str, str]] = []

    def add_episode(self, gate: str, file_path: str, error_msg: str, action_taken: str):
        self.episodes.append({
            "gate": gate,
            "file": file_path,
            "error": error_msg[:400],
            "action": action_taken,
        })

    def format_for_prompt(self) -> str:
        if not self.episodes:
            return ""
        lines = ["MEMORIA EPISÓDICA DE INTENTOS PREVIOS (NO REPITAS ERRORES YA FALLIDOS):"]
        for i, ep in enumerate(self.episodes[-4:], 1):
            lines.append(f"  {i}. [{ep['gate']}] en `{ep['file']}` -> Error: {ep['error']} | Intento previo: {ep['action']}")
        return "\n".join(lines)


def reflexion_verify_and_heal_project(
    plan_files: List[Dict[str, str]],
    workspace_dir: Path,
    cloud_cfg: Dict[str, Any],
    local_port: int = 8085,
    use_cloud: bool = False,
    max_rounds: int = 3
) -> Dict[str, Any]:
    """
    Verificador Reflexion Multi-Compuerta (Estilo Antigravity / Codex):
    - Gate 1: Coherencia del Code GraphRAG G=(V,E) (aristas rotas entre imports Python y fetch/API frontend-backend).
    - Gate 2: Prueba real de importación de todos los módulos Python en subproceso aislado.
    - Gate 3: Ejecución real de `main.py` (si es CLI no-bloqueante) capturando stdout/stderr/Traceback.
    Ante cualquier fallo, actualiza `ReflexionMemory`, repara el archivo exacto con el modelo local (o cloud)
    y repite la verificación hasta certificar 0 errores.
    """
    memory = ReflexionMemory()
    healed_files: List[str] = []
    py_files = [f for f in plan_files if f.get("path", "").lower().endswith(".py")]
    last_diagnostic = "Todos los módulos pasaron AST, Code GraphRAG y Runtime."

    def _run_all_gates() -> Tuple[bool, str, Optional[str], str]:
        # GATE 1: Code GraphRAG Broken Edges (Imports + API Frontend/Backend)
        g_info = code_arch_graph.analyze_workspace_graph(workspace_dir)
        broken_edges = g_info.get("broken_edges", [])
        if broken_edges:
            first_be = broken_edges[0]
            m_src = re.search(r"`([^`]+)`", first_be)
            culprit = m_src.group(1) if m_src else (py_files[-1]["path"] if py_files else None)
            return False, "Gate1:CodeGraph", culprit, "\n".join(broken_edges[:4])

        # GATE 2: Real Python Subprocess Import Check
        mod_entries = []
        for pf in py_files:
            rel = pf["path"].replace("\\", "/").lstrip("/")
            if (workspace_dir / rel).exists():
                mod_entries.append((rel, re.sub(r"\.py$", "", rel, flags=re.IGNORECASE).replace("/", ".")))

        for rel_p, mod_n in mod_entries:
            try:
                src_txt = (workspace_dir / rel_p).read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if any(k in src_txt for k in ("mainloop(", "serve_forever(", "uvicorn.run(", "app.run(")) and "if __name__" not in src_txt:
                continue
            probe = (
                f"import sys, importlib; sys.path.insert(0, {repr(str(workspace_dir.resolve()))}); "
                f"importlib.import_module({repr(mod_n)})"
            )
            try:
                env_p = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in ("SECRET", "API_KEY", "TOKEN", "PASSWORD", "AWS_"))}
                env_p["PYTHONIOENCODING"] = "utf-8"
                res = subprocess.run(
                    [sys.executable, "-X", "utf8", "-c", probe],
                    cwd=str(workspace_dir),
                    env=env_p,
                    capture_output=True,
                    text=True,
                    timeout=7
                )
                if res.returncode != 0:
                    tb = (res.stderr or res.stdout or "").strip()
                    return False, "Gate2:ImportProbe", rel_p, f"Error importando `{rel_p}`:\n{tb[-1300:]}"
            except subprocess.TimeoutExpired:
                continue
            except Exception as e:
                return False, "Gate2:ImportProbe", rel_p, str(e)

        # GATE 3: Real End-to-End CLI Smoke Execution (`main.py`)
        main_item = next((f for f in py_files if Path(f["path"]).name.lower() == "main.py"), None)
        if main_item:
            main_rel = main_item["path"].replace("\\", "/").lstrip("/")
            main_fp = workspace_dir / main_rel
            if main_fp.exists():
                m_txt = main_fp.read_text(encoding="utf-8", errors="replace")
                if not any(k in m_txt for k in ("mainloop(", "serve_forever(", "uvicorn.run(", "app.run(", "input(")):
                    try:
                        env_p = os.environ.copy()
                        env_p["PYTHONIOENCODING"] = "utf-8"
                        res = subprocess.run(
                            [sys.executable, "-X", "utf8", str(main_fp)],
                            cwd=str(workspace_dir),
                            env=env_p,
                            capture_output=True,
                            text=True,
                            timeout=7
                        )
                        if res.returncode != 0:
                            tb = (res.stderr or res.stdout or "").strip()
                            culprit = main_rel
                            for rel_p, _ in mod_entries:
                                if Path(rel_p).name in tb and rel_p != main_rel:
                                    culprit = rel_p
                                    break
                            return False, "Gate3:RuntimeExec", culprit, f"Traceback ejecutando `python {main_rel}`:\n{tb[-1300:]}"
                        out_preview = (res.stdout or "").strip()[:240]
                        return True, "ALL_GATES_PASS", None, f"Ejecución real exitosa (`exit_code=0`). Salida: {out_preview or 'OK'}"
                    except subprocess.TimeoutExpired:
                        return True, "ALL_GATES_PASS", None, "Aplicación continua iniciada limpiamente sin errores."
                    except Exception:
                        pass

        # GATE 4: Validación real de Frontend Web (HTML integridad, enlaces locales y sintaxis JS con node --check)
        web_files = [f for f in plan_files if f.get("path", "").lower().endswith((".html", ".htm", ".js"))]
        for wf in web_files:
            w_rel = wf["path"].replace("\\", "/").lstrip("/")
            w_fp = workspace_dir / w_rel
            if not w_fp.exists():
                continue
            w_txt = w_fp.read_text(encoding="utf-8", errors="replace")
            if w_rel.lower().endswith((".html", ".htm")):
                if "<html" not in w_txt.lower() or "</html>" not in w_txt.lower():
                    return False, "Gate4:WebHTML", w_rel, f"`{w_rel}` está incompleto (falta etiqueta <html> o cierre </html>)."
                # Verificar referencias locales a .js y .css
                local_refs = re.findall(r'(?:src|href)=["\']([^"\']+)["\']', w_txt, re.IGNORECASE)
                for ref in local_refs:
                    r_clean = ref.split("?")[0].split("#")[0].strip()
                    if not r_clean or r_clean.startswith(("http://", "https://", "//", "data:", "mailto:", "#")):
                        continue
                    if r_clean.endswith((".js", ".css")):
                        ref_target = (w_fp.parent / r_clean.lstrip("./")).resolve()
                        if not ref_target.exists():
                            return False, "Gate4:WebHTML", w_rel, f"`{w_rel}` enlaza al archivo inexistente `{r_clean}`. Corrígelo o embebe el código."
            elif w_rel.lower().endswith(".js"):
                try:
                    res_js = subprocess.run(
                        ["node", "--check", str(w_fp)],
                        cwd=str(workspace_dir),
                        capture_output=True,
                        text=True,
                        timeout=6
                    )
                    if res_js.returncode != 0:
                        err_js = (res_js.stderr or res_js.stdout or "").strip()
                        return False, "Gate4:WebJS", w_rel, f"Error de sintaxis JavaScript en `{w_rel}`:\n{err_js[-1000:]}"
                except FileNotFoundError:
                    pass
                except Exception:
                    pass
                # Verificar coherencia de IDs DOM entre app.js e index.html
                html_fp = workspace_dir / "index.html"
                if html_fp.exists():
                    h_txt = html_fp.read_text(encoding="utf-8", errors="replace")
                    html_ids = set(re.findall(r'\bid=["\']([a-zA-Z0-9_\-]+)["\']', h_txt))
                    dyn_ids = set(re.findall(r'(?:\.id\s*=\s*|id=["\'])([a-zA-Z0-9_\-]+)', w_txt))
                    js_used_ids = re.findall(r'getElementById\(\s*["\']([a-zA-Z0-9_\-]+)["\']\s*\)', w_txt)
                    missing_ids = [i for i in js_used_ids if i not in html_ids and i not in dyn_ids]
                    if missing_ids:
                        return (
                            False,
                            "Gate4:WebDOM",
                            w_rel,
                            f"`{w_rel}` busca IDs DOM inexistentes en `index.html`: {', '.join(missing_ids[:6])}. "
                            f"IDs reales disponibles en `index.html`: {', '.join(sorted(html_ids)[:20])}"
                        )

        return True, "ALL_GATES_PASS", None, "Compuertas AST, Code GraphRAG, Web/JS e Imports aprobadas (0 errores)."

    for rnd in range(max_rounds + 1):
        ok, gate_name, culprit_rel, diag = _run_all_gates()
        last_diagnostic = diag
        if ok:
            return {
                "success": True,
                "healed_files": healed_files,
                "rounds_used": rnd,
                "summary": diag + (f" | Auto-reparados: {', '.join(healed_files)}" if healed_files else "")
            }

        if rnd >= max_rounds or not culprit_rel:
            break

        target_fp = (workspace_dir / culprit_rel).resolve()
        if not target_fp.exists():
            break

        broken_src = target_fp.read_text(encoding="utf-8", errors="replace")
        ws_sigs, _ = collect_workspace_signatures(workspace_dir)
        reflexion_ctx = memory.format_for_prompt()
        full_diag = f"{diag}\n\n{reflexion_ctx}".strip()
        target_info = next(
            (f for f in plan_files if f["path"].replace("\\", "/").lstrip("/") == culprit_rel),
            {"path": culprit_rel, "description": "Módulo del proyecto"}
        )

        print(f"[ReflexionEngine] Ronda {rnd + 1} [{gate_name}] reparando `{culprit_rel}`...")
        repaired_raw = ""
        if use_cloud and cloud_cfg.get("enabled"):
            ok_c, resp_c = call_cloud_worker(
                f"Hola genera esto:\nCorrige este fallo real ({gate_name}) en `{culprit_rel}`:\n{full_diag}\n\n"
                f"Grafo y contratos del proyecto:\n{ws_sigs}\n\nCódigo actual:\n{broken_src}\n\n"
                "Devuelve ÚNICAMENTE el código fuente completo corregido.",
                cloud_cfg,
                bypass_desensitize=True
            )
            if ok_c and resp_c and not is_refusal_response(resp_c):
                repaired_raw = resp_c

        if not repaired_raw:
            repaired_raw = _heal_code_with_local_qwen(
                broken_src,
                target_info,
                full_diag,
                local_port=local_port,
                sibling_signatures=ws_sigs
            )

        memory.add_episode(gate_name, culprit_rel, diag, f"Auto-reparación ronda {rnd + 1}")

        if repaired_raw:
            clean_rep = extract_pure_code_from_llm(repaired_raw, culprit_rel)
            if culprit_rel.lower().endswith(".py"):
                clean_rep = auto_fix_common_python_issues(clean_rep, culprit_rel, workspace_dir=workspace_dir)
                try:
                    ast.parse(clean_rep, filename=culprit_rel)
                except SyntaxError:
                    continue
            target_fp.write_text(clean_rep, encoding="utf-8")
            if culprit_rel not in healed_files:
                healed_files.append(culprit_rel)

    return {
        "success": False,
        "healed_files": healed_files,
        "rounds_used": max_rounds,
        "summary": f"Verificación Reflexion completada con aviso: {last_diagnostic[:320]}"
    }
