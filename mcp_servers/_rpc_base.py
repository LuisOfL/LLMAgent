# -*- coding: utf-8 -*-
"""
mcp_servers/_rpc_base.py — Servidor base JSON-RPC 2.0 sobre stdio para el protocolo MCP (2024-11-05).
"""

import json
import sys
from typing import Any, Callable, Dict, List

MCP_PROTOCOL_VERSION = "2024-11-05"


def run_stdio_mcp_server(
    server_name: str,
    version: str,
    tools: List[Dict[str, Any]],
    handlers: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]],
):
    """Bucle principal JSON-RPC 2.0 sobre stdin/stdout para cualquier servidor MCP desacoplado."""
    def _send(obj: Dict[str, Any]):
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        req_id = None
        try:
            msg = json.loads(line)
            req_id = msg.get("id")
            method = msg.get("method", "")
            params = msg.get("params") or {}

            if method == "initialize":
                _send({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": server_name, "version": version},
                    },
                })
            elif method == "notifications/initialized":
                continue
            elif method == "tools/list":
                _send({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"tools": tools},
                })
            elif method == "tools/call":
                t_name = str(params.get("name", ""))
                t_args = params.get("arguments") or {}
                fn = handlers.get(t_name)
                if not fn:
                    _send({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32601, "message": f"Tool desconocida en {server_name}: {t_name}"},
                    })
                    continue
                try:
                    out = fn(t_args)
                    is_err = not bool(out.get("success", True)) if isinstance(out, dict) else False
                    _send({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}],
                            "isError": is_err,
                        },
                    })
                except Exception as exc:
                    _send({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{"type": "text", "text": json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)}],
                            "isError": True,
                        },
                    })
            elif req_id is not None:
                _send({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Método no soportado: {method}"},
                })
        except Exception as e:
            _send({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32700, "message": f"JSON-RPC parse error: {e}"},
            })
