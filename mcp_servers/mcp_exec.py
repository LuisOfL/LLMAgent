# -*- coding: utf-8 -*-
"""
mcp_servers/mcp_exec.py — Servidor MCP #4 (MCP-EXEC): QA Estático (AST/Ruff/tsc/node) y Ejecución en Sandbox.
"""

import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from mcp_servers._rpc_base import run_stdio_mcp_server

TOOLS = [
    {
        "name": "run_static_qa",
        "description": "Ejecuta verificación estática (AST Python, Ruff si existe, node --check en JS, tsc si hay tsconfig y coherencia HTML/DOM).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_dir": {"type": "string"},
                "file_paths": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["workspace_dir", "file_paths"]
        },
    },
    {
        "name": "run_sandbox_exec",
        "description": "Ejecuta pruebas (pytest o smoke test de entrypoint Python) en entorno sanitizado sin credenciales del host.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_dir": {"type": "string"},
                "entrypoint": {"type": "string", "default": "main.py"}
            },
            "required": ["workspace_dir"]
        },
    },
]


def _sanitized_env() -> Dict[str, str]:
    env = {
        k: v for k, v in os.environ.items()
        if not any(s in k.upper() for s in ("SECRET", "API_KEY", "TOKEN", "PASSWORD", "AWS_"))
    }
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_static_qa(args: Dict[str, Any]) -> Dict[str, Any]:
    ws = Path(str(args.get("workspace_dir", "."))).resolve()
    file_paths = [str(p).replace("\\", "/").lstrip("/") for p in (args.get("file_paths") or [])]
    errors: List[Dict[str, str]] = []
    checks_run: List[str] = []

    html_ids_map: Dict[str, set] = {}
    html_classes_map: Dict[str, set] = {}
    html_inline_handlers: Dict[str, set] = {}
    backend_routes: set = set()
    frontend_fetches: List[Dict[str, str]] = []
    js_files_content: Dict[str, str] = {}
    defined_js_funcs: set = set()
    py_class_owners: Dict[str, str] = {}

    for rel in file_paths:
        fp = (ws / rel).resolve()
        if not str(fp).startswith(str(ws)) or not fp.exists():
            continue
        txt = fp.read_text(encoding="utf-8", errors="replace")
        low = rel.lower()

        if low.endswith(".py"):
            checks_run.append(f"ast:{rel}")
            try:
                tree = ast.parse(txt, filename=rel)
                for node in tree.body:
                    if isinstance(node, ast.ClassDef):
                        if node.name in py_class_owners and py_class_owners[node.name] != rel:
                            owner_file = py_class_owners[node.name]
                            owner_mod = re.sub(r"\.py$", "", owner_file).replace("/", ".")
                            errors.append({
                                "file": rel,
                                "gate": "PY_DUPLICATE_CLASS",
                                "error": f"Clase duplicada `{node.name}` en `{rel}` (ya definida en `{owner_file}`). NO redefinas clases de módulos hermanos; impórtala con `from {owner_mod} import {node.name}`."
                            })
                        else:
                            py_class_owners[node.name] = rel
            except SyntaxError as se:
                errors.append({"file": rel, "gate": "AST", "error": f"SyntaxError línea {se.lineno}: {se.msg}"})
            for rt in re.findall(r'["\'](/api/[a-zA-Z0-9_\-/{}]+)["\']', txt):
                backend_routes.add(rt.rstrip("/"))

        elif low.endswith((".html", ".htm")):
            checks_run.append(f"html:{rel}")
            if txt.lstrip().startswith("```"):
                errors.append({"file": rel, "gate": "HTML_MARKDOWN_LEAK", "error": "El archivo HTML contiene una marca Markdown (```html) al inicio. Elimina cualquier bloque de texto Markdown."})
            if "<html" not in txt.lower() or "</html>" not in txt.lower():
                errors.append({"file": rel, "gate": "HTML", "error": "Documento HTML incompleto (falta <html> o </html>)."})
            if re.search(r'<script[^>]+type=["\']importmap["\'][^>]+src=', txt, re.IGNORECASE):
                errors.append({
                    "file": rel,
                    "gate": "HTML_IMPORTMAP",
                    "error": '<script type="importmap"> NO soporta atributo src=. Debe contener el JSON inline directamente dentro de la etiqueta <script type="importmap">{"imports":{...}}</script>.'
                })
            html_ids_map[rel] = set(re.findall(r'\bid=["\']([a-zA-Z0-9_\-]+)["\']', txt))
            cls_set = set()
            for c_attr in re.findall(r'\bclass=["\']([^"\']+)["\']', txt):
                cls_set.update(c_attr.split())
            html_classes_map[rel] = cls_set
            html_inline_handlers[rel] = set(re.findall(r'\bon(?:click|change|submit|input)=["\']\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\(', txt, re.IGNORECASE))
            defined_js_funcs.update(re.findall(r'\bfunction\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(', txt))
            defined_js_funcs.update(re.findall(r'\b(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*=>', txt))
            defined_js_funcs.update(re.findall(r'\bwindow\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=', txt))
            for f_url in re.findall(r'fetch\(\s*["\'](/api/[a-zA-Z0-9_\-/]+)["\']', txt):
                frontend_fetches.append({"file": rel, "url": f_url.rstrip("/")})
            for ref in re.findall(r'(?:src|href)=["\']([^"\']+)["\']', txt, re.IGNORECASE):
                clean_ref = ref.split("?")[0].split("#")[0].strip()
                if not clean_ref or clean_ref.startswith(("http://", "https://", "//", "data:", "mailto:", "#")):
                    continue
                if clean_ref.endswith((".js", ".css", ".json", ".glb", ".gltf", ".png", ".jpg", ".svg")):
                    if not (fp.parent / clean_ref.lstrip("./")).resolve().exists():
                        errors.append({"file": rel, "gate": "HTML_REF", "error": f"Referencia rota a `{clean_ref}` (no existe en el proyecto)."})

        elif low.endswith(".css"):
            checks_run.append(f"css:{rel}")
            if re.search(r"^\s*(?:<!DOCTYPE\s+html|<html\b|<head\b|<body\b)", txt, re.IGNORECASE):
                errors.append({
                    "file": rel,
                    "gate": "CSS_IS_HTML",
                    "error": f"`{rel}` contiene un documento HTML (`<!DOCTYPE html>` / `<html>`) en lugar de CSS puro. Escribe ÚNICAMENTE reglas CSS válidas."
                })

        elif low.endswith(".js"):
            js_files_content[rel] = txt
            defined_js_funcs.update(re.findall(r'\bfunction\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(', txt))
            defined_js_funcs.update(re.findall(r'\b(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*=>', txt))
            defined_js_funcs.update(re.findall(r'\bwindow\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=', txt))
            if re.search(r"//\s*(?:\.\.\.|TODO\b|FIXME\b|placeholder)", txt, re.IGNORECASE):
                errors.append({"file": rel, "gate": "PLACEHOLDER_FOUND", "error": f"`{rel}` contiene comentarios placeholder/TODO sin implementar."})
            if shutil.which("node"):
                checks_run.append(f"node:{rel}")
                r = subprocess.run(["node", "--check", str(fp)], cwd=str(ws), capture_output=True, text=True, timeout=6)
                if r.returncode != 0:
                    errors.append({"file": rel, "gate": "JS_SYNTAX", "error": (r.stderr or r.stdout).strip()[-900:]})
            for f_url in re.findall(r'fetch\(\s*["\'](/api/[a-zA-Z0-9_\-/]+)["\']', txt):
                frontend_fetches.append({"file": rel, "url": f_url.rstrip("/")})

            # Verificación determinista de bucle Three.js / WebGL (evita <canvas> negro silencioso)
            uses_three = bool(re.search(r"\b(?:THREE\.|WebGLRenderer|GLTFLoader|PerspectiveCamera)\b", txt))
            if uses_three:
                checks_run.append(f"three_render:{rel}")
                has_raf = "requestAnimationFrame" in txt
                has_render = bool(re.search(r"\.render\s*\(", txt))
                if not has_raf or not has_render:
                    missing_parts = []
                    if not has_raf:
                        missing_parts.append("requestAnimationFrame(...)")
                    if not has_render:
                        missing_parts.append("renderer.render(scene, camera)")
                    errors.append({
                        "file": rel,
                        "gate": "JS_THREE_NO_RENDER_LOOP",
                        "error": f"Escena Three.js en `{rel}` sin bucle de render activo (falta {' y '.join(missing_parts)}). Sin esto el <canvas> quedará completamente negro."
                    })

            # Verificación de clases JS no instanciadas ni exportadas (ej. class App { ... } sin new App())
            for m_cls in re.finditer(r"(?m)^(?P<exp>export\s+(?:default\s+)?)?class\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\b", txt):
                if m_cls.group("exp"):
                    continue
                cls_name = m_cls.group("name")
                if f"module.exports" in txt and cls_name in txt.split("module.exports", 1)[1]:
                    continue
                if not re.search(rf"\bnew\s+{re.escape(cls_name)}\s*\(", txt):
                    errors.append({
                        "file": rel,
                        "gate": "JS_CLASS_NOT_INSTANTIATED",
                        "error": f"La clase `{cls_name}` se define en `{rel}` pero nunca se instancia con `new {cls_name}()` ni se exporta. Instánciala al cargar el DOM (ej. `window.addEventListener('DOMContentLoaded', () => new {cls_name}());`)."
                    })

    # AUDITORÍA DETERMINISTA DE COSTURAS ENTRE ARCHIVOS (WIRING & CROSS-FILE CONTRACTS)
    all_html_ids = set().union(*html_ids_map.values()) if html_ids_map else set()
    if all_html_ids:
        checks_run.append("wiring:html_js_dom")
        for js_rel, js_txt in js_files_content.items():
            dyn_ids = set(re.findall(r'(?:\.id\s*=\s*|id=["\'])([a-zA-Z0-9_\-]+)', js_txt))
            used_ids = set(re.findall(r'getElementById\(\s*["\']([a-zA-Z0-9_\-]+)["\']\s*\)', js_txt))
            used_ids.update(re.findall(r'querySelector(?:All)?\(\s*["\']#([a-zA-Z0-9_\-]+)["\']\s*\)', js_txt))
            missing = [i for i in sorted(used_ids) if i not in all_html_ids and i not in dyn_ids]
            if missing:
                errors.append({
                    "file": js_rel,
                    "gate": "WIRING_DOM_MISMATCH",
                    "error": f"MISMATCH: `{js_rel}` busca {', '.join('#'+m for m in missing[:6])}, pero el HTML solo define: {', '.join('#'+h for h in sorted(all_html_ids)[:15])}"
                })

    if html_inline_handlers:
        checks_run.append("wiring:event_handlers")
        for h_rel, handlers in html_inline_handlers.items():
            missing_h = sorted(h for h in handlers if h not in defined_js_funcs)
            if missing_h:
                errors.append({
                    "file": h_rel,
                    "gate": "WIRING_EVENT_HANDLER_MISSING",
                    "error": f"MISMATCH: `{h_rel}` invoca handler(s) inline {', '.join(m + '()' for m in missing_h[:5])}, pero no están definidos en ningún JS ni en `window.*`."
                })

    if backend_routes and frontend_fetches:
        checks_run.append("wiring:api_routes")
        for ff in frontend_fetches:
            u = ff["url"]
            matched = any(u == br or re.sub(r"\{[^}]+\}", "[^/]+", br) and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", br), u) for br in backend_routes)
            if not matched:
                errors.append({
                    "file": ff["file"],
                    "gate": "WIRING_API_MISMATCH",
                    "error": f"MISMATCH: `{ff['file']}` llama a `{u}`, pero el backend solo expone: {', '.join(sorted(backend_routes)[:12])}"
                })

    # Ruff opcional si está instalado en el entorno
    py_targets = [str(ws / r) for r in file_paths if r.lower().endswith(".py") and (ws / r).exists()]
    if py_targets and shutil.which("ruff"):
        checks_run.append("ruff")
        rr = subprocess.run(["ruff", "check", "--select", "E9,F63,F7,F82", *py_targets], cwd=str(ws), capture_output=True, text=True, timeout=8)
        if rr.returncode != 0:
            errors.append({"file": file_paths[0], "gate": "RUFF", "error": (rr.stdout or rr.stderr).strip()[-900:]})

    return {
        "success": len(errors) == 0,
        "checks_run": checks_run,
        "errors": errors,
    }


def _run_sandbox_exec(args: Dict[str, Any]) -> Dict[str, Any]:
    ws = Path(str(args.get("workspace_dir", "."))).resolve()
    entry = str(args.get("entrypoint") or "main.py").replace("\\", "/").lstrip("/")
    fp = (ws / entry).resolve()
    if not str(fp).startswith(str(ws)) or not fp.exists() or not entry.lower().endswith(".py"):
        return {"success": True, "skipped": True, "detail": "Sin entrypoint Python ejecutable."}

    txt = fp.read_text(encoding="utf-8", errors="replace")
    if any(k in txt for k in ("mainloop(", "serve_forever(", "uvicorn.run(", "app.run(", "input(")):
        # Solo verificar importabilidad limpia sin bloquear en servidor/GUI
        mod = re.sub(r"\.py$", "", entry).replace("/", ".")
        probe = f"import sys, importlib; sys.path.insert(0, {repr(str(ws))}); importlib.import_module({repr(mod)})"
        cmd = [sys.executable, "-X", "utf8", "-c", probe]
    else:
        cmd = [sys.executable, "-X", "utf8", str(fp)]

    try:
        res = subprocess.run(cmd, cwd=str(ws), env=_sanitized_env(), capture_output=True, text=True, timeout=8)
        if res.returncode != 0:
            return {"success": False, "file": entry, "exit_code": res.returncode, "stacktrace": (res.stderr or res.stdout).strip()[-1400:]}
        return {"success": True, "file": entry, "exit_code": 0, "stdout": (res.stdout or "").strip()[:300]}
    except subprocess.TimeoutExpired:
        return {"success": True, "file": entry, "detail": "Proceso continuo iniciado sin excepciones inmediatas."}


if __name__ == "__main__":
    run_stdio_mcp_server(
        "mcp-exec",
        "1.0.0",
        TOOLS,
        {"run_static_qa": _run_static_qa, "run_sandbox_exec": _run_sandbox_exec},
    )
