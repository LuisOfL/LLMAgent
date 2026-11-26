# -*- coding: utf-8 -*-
"""
scripts/example_mcp_server.py — Servidor MCP real mínimo (JSON-RPC 2.0 sobre stdio).

Demuestra cómo conectar un proceso externo desacoplado a LLMAgent mediante el protocolo
oficial Model Context Protocol (`initialize`, `tools/list`, `tools/call`).
"""

import json
import platform
import sys
from datetime import datetime, timezone


TOOLS = [
    {
        "name": "system_clock",
        "description": "Devuelve la fecha, hora UTC/local y plataforma del sistema desde un servidor MCP externo.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        },
    },
    {
        "name": "word_stats",
        "description": "Calcula estadísticas exactas de texto (palabras, líneas y caracteres) en el servidor MCP externo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Texto a analizar."}
            },
            "required": ["text"]
        },
    },
]


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_request(msg):
    method = msg.get("method", "")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        send({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "llmagent-example-mcp", "version": "1.0.0"},
            },
        })
    elif method == "notifications/initialized":
        return
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        if name == "system_clock":
            payload = {
                "utc": datetime.now(timezone.utc).isoformat(),
                "platform": platform.platform(),
                "python": platform.python_version(),
            }
            send({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], "isError": False},
            })
        elif name == "word_stats":
            txt = str(args.get("text", ""))
            payload = {"chars": len(txt), "words": len(txt.split()), "lines": len(txt.splitlines())}
            send({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], "isError": False},
            })
        else:
            send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Tool desconocida: {name}"}})
    elif req_id is not None:
        send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Método no soportado: {method}"}})


def main():
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            handle_request(json.loads(line))
        except Exception as e:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(e)}})


if __name__ == "__main__":
    main()
