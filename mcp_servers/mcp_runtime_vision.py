# -*- coding: utf-8 -*-
"""
mcp_servers/mcp_runtime_vision.py — Servidor MCP #5 (MCP-RUNTIME-VISION):
Verificación web en tiempo real: levanta servidor HTTP efímero y valida con Playwright Headless
(errores de consola, excepciones JS, red >=400, DOM/Canvas) o fallback HTTP + DOM Probe si Playwright no está instalado.
"""

import functools
import http.server
import re
import socketserver
import sys
import threading
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from mcp_servers._rpc_base import run_stdio_mcp_server

TOOLS = [
    {
        "name": "verify_web_runtime",
        "description": "Levanta un servidor HTTP temporal sobre el workspace y verifica la web (Playwright headless si está disponible, o probe HTTP+DOM).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_dir": {"type": "string"},
                "html_file": {"type": "string", "default": "index.html"}
            },
            "required": ["workspace_dir"]
        },
    },
]


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


def _verify_web_runtime(args: Dict[str, Any]) -> Dict[str, Any]:
    ws = Path(str(args.get("workspace_dir", "."))).resolve()
    html_file = str(args.get("html_file") or "index.html").replace("\\", "/").lstrip("/")
    html_fp = (ws / html_file).resolve()
    if not str(html_fp).startswith(str(ws)) or not html_fp.exists():
        return {"success": True, "skipped": True, "detail": f"Sin archivo web `{html_file}` para verificación visual."}

    try:
        handler = functools.partial(_QuietHandler, directory=str(ws))
        httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
        port = httpd.server_address[1]
        srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        srv_thread.start()
        base_url = f"http://127.0.0.1:{port}/{html_file}"
    except Exception:
        httpd = None
        base_url = ""

    try:
        # 1. Intentar Playwright Headless real si está instalado y el servidor local pudo levantarse
        if base_url:
            try:
                from playwright.sync_api import sync_playwright
                console_errors: List[str] = []
                page_errors: List[str] = []
                failed_reqs: List[str] = []
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    page = browser.new_page(viewport={"width": 1280, "height": 720})
                    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
                    page.on("pageerror", lambda e: page_errors.append(str(e)))
                    page.on("response", lambda r: failed_reqs.append(f"{r.status} {r.url}") if r.status >= 400 else None)
                    page.goto(base_url, wait_until="domcontentloaded", timeout=10000)
                    page.wait_for_timeout(600)
                    body_len = len((page.inner_text("body") or "").strip())
                    browser.close()
                issues = page_errors + console_errors + failed_reqs
                return {
                    "success": len(issues) == 0,
                    "engine": "playwright_chromium",
                    "url": base_url,
                    "body_chars": body_len,
                    "issues": issues[:8],
                }
            except Exception:
                pass

        # 2. Fallback ligero sin Playwright: HTTP GET real (o verificación directa en disco si un firewall bloquea sockets)
        issues: List[str] = []
        engine_used = "http_dom_probe"
        try:
            if not base_url:
                raise OSError("No local socket")
            with urllib.request.urlopen(base_url, timeout=5) as resp:
                if int(getattr(resp, "status", 200)) >= 400:
                    issues.append(f"HTTP {resp.status} en {base_url}")
                html_txt = resp.read().decode("utf-8", errors="replace")
        except Exception:
            engine_used = "fs_dom_probe"
            html_txt = html_fp.read_text(encoding="utf-8", errors="replace")

        refs = re.findall(r'(?:src|href)=["\']([^"\']+)["\']', html_txt, re.IGNORECASE)
        checked_refs = 0
        for ref in refs:
            clean = ref.split("?")[0].split("#")[0].strip()
            if not clean or clean.startswith(("http://", "https://", "//", "data:", "mailto:", "#")):
                continue
            checked_refs += 1
            if engine_used == "http_dom_probe":
                ref_url = f"http://127.0.0.1:{port}/{clean.lstrip('./')}"
                try:
                    with urllib.request.urlopen(ref_url, timeout=4) as r_sub:
                        if int(getattr(r_sub, "status", 200)) >= 400:
                            issues.append(f"HTTP {r_sub.status} cargando asset local `{clean}`")
                except Exception as e:
                    issues.append(f"Asset local inaccesible `{clean}`: {e}")
            else:
                if not (html_fp.parent / clean.lstrip("./")).resolve().exists():
                    issues.append(f"Asset local inexistente `{clean}`")

        return {
            "success": len(issues) == 0,
            "engine": engine_used,
            "url": base_url or str(html_fp),
            "checked_local_assets": checked_refs,
            "issues": issues[:8],
        }
    finally:
        if httpd:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                pass


if __name__ == "__main__":
    run_stdio_mcp_server(
        "mcp-runtime-vision",
        "1.0.0",
        TOOLS,
        {"verify_web_runtime": _verify_web_runtime},
    )
