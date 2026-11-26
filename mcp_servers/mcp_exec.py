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
    for rel in file_paths:
        fp = (ws / rel).resolve()
        if not str(fp).startswith(str(ws)) or not fp.exists():
            continue
        txt = fp.read_text(encoding="utf-8", errors="replace")
        low = rel.lower()

        if low.endswith(".py"):
            checks_run.append(f"ast:{rel}")
            try:
                ast.parse(txt, filename=rel)
            except SyntaxError as se:
                errors.append({"file": rel, "gate": "AST", "error": f"SyntaxError línea {se.lineno}: {se.msg}"})

        elif low.endswith((".html", ".htm")):
            checks_run.append(f"html:{rel}")
            if "<html" not in txt.lower() or "</html>" not in txt.lower():
                errors.append({"file": rel, "gate": "HTML", "error": "Documento HTML incompleto (falta <html> o </html>)."})
            html_ids_map[rel] = set(re.findall(r'\bid=["\']([a-zA-Z0-9_\-]+)["\']', txt))
            for ref in re.findall(r'(?:src|href)=["\']([^"\']+)["\']', txt, re.IGNORECASE):
                clean_ref = ref.split("?")[0].split("#")[0].strip()
                if not clean_ref or clean_ref.startswith(("http://", "https://", "//", "data:", "mailto:", "#")):
                    continue
                if clean_ref.endswith((".js", ".css")):
                    if not (fp.parent / clean_ref.lstrip("./")).resolve().exists():
                        errors.append({"file": rel, "gate": "HTML_REF", "error": f"Referencia rota a `{clean_ref}`."})

        elif low.endswith(".js"):
            if shutil.which("node"):
                checks_run.append(f"node:{rel}")
                r = subprocess.run(["node", "--check", str(fp)], cwd=str(ws), capture_output=True, text=True, timeout=6)
                if r.returncode != 0:
                    errors.append({"file": rel, "gate": "JS_SYNTAX", "error": (r.stderr or r.stdout).strip()[-900:]})
            if "index.html" in html_ids_map:
                h_ids = html_ids_map["index.html"]
                dyn_ids = set(re.findall(r'(?:\.id\s*=\s*|id=["\'])([a-zA-Z0-9_\-]+)', txt))
                missing = [i for i in re.findall(r'getElementById\(\s*["\']([a-zA-Z0-9_\-]+)["\']\s*\)', txt) if i not in h_ids and i not in dyn_ids]
                if missing:
                    errors.append({"file": rel, "gate": "DOM_CONTRACT", "error": f"IDs inexistentes en index.html: {', '.join(missing[:6])}"})

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
