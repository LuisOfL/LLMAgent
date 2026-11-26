# -*- coding: utf-8 -*-
"""
core/mcp_client.py — Cliente real de Model Context Protocol (MCP, JSON-RPC 2.0).

Soporta servidores MCP externos desacoplados mediante:
- Transporte `stdio` (subprocesos estándar como `@modelcontextprotocol/server-github`,
  `@modelcontextprotocol/server-postgres`, `@playwright/mcp`, servidores Python locales, etc.)
- Transporte `http` (JSON-RPC 2.0 sobre HTTP/SSE hacia servidores MCP remotos o locales)

Lee la configuración desde `mcp_servers.json` en la raíz del proyecto usando el esquema estándar:
{
  "mcpServers": {
    "mi_servidor": {
      "command": "python",
      "args": ["scripts/example_mcp_server.py"],
      "enabled": true
    }
  }
}
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
MCP_CONFIG_PATH = BASE_DIR / "mcp_servers.json"
MCP_PROTOCOL_VERSION = "2024-11-05"


class StdioMCPConnection:
    """Conexión JSON-RPC 2.0 real sobre stdio hacia un servidor MCP externo."""

    def __init__(self, name: str, command: str, args: Optional[List[str]] = None, env: Optional[Dict[str, str]] = None, cwd: Optional[str] = None):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.cwd = cwd or str(BASE_DIR)
        self.proc: Optional[subprocess.Popen] = None
        self._req_id = 0
        self._lock = threading.Lock()
        self._pending: Dict[int, "queue.Queue[Dict[str, Any]]"] = {}
        self._reader_thread: Optional[threading.Thread] = None
        self.tools: List[Dict[str, Any]] = []
        self.server_info: Dict[str, Any] = {}
        self.connected = False
        self.last_error = ""

    def connect(self, timeout: float = 15.0) -> bool:
        try:
            cmd_bin = sys.executable if self.command in ("python", "python3") else self.command
            full_env = os.environ.copy()
            full_env["PYTHONIOENCODING"] = "utf-8"
            full_env.update({str(k): str(v) for k, v in self.env.items()})

            self.proc = subprocess.Popen(
                [cmd_bin, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
                env=full_env,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._reader_thread.start()

            # 1. Handshake oficial MCP: initialize
            init_res = self._request(
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "LLMAgent", "version": "2.0.0"},
                },
                timeout=timeout,
            )
            self.server_info = init_res.get("serverInfo") or {}

            # 2. Notificación obligatoria: notifications/initialized
            self._notify("notifications/initialized", {})

            # 3. Descubrimiento dinámico de herramientas: tools/list
            tools_res = self._request("tools/list", {}, timeout=timeout)
            self.tools = tools_res.get("tools") or []
            self.connected = True
            self.last_error = ""
            return True
        except Exception as e:
            self.last_error = str(e)
            self.connected = False
            self.close()
            return False

    def _read_loop(self):
        if not self.proc or not self.proc.stdout:
            return
        for raw_line in self.proc.stdout:
            line = raw_line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            req_id = msg.get("id")
            if req_id is not None:
                with self._lock:
                    q = self._pending.get(req_id)
                if q is not None:
                    q.put(msg)

    def _notify(self, method: str, params: Dict[str, Any]):
        if not self.proc or not self.proc.stdin:
            raise RuntimeError("Servidor MCP stdio no conectado.")
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        with self._lock:
            self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()

    def _request(self, method: str, params: Dict[str, Any], timeout: float = 20.0) -> Dict[str, Any]:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError("Servidor MCP stdio no conectado.")
        q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=1)
        with self._lock:
            self._req_id += 1
            rid = self._req_id
            self._pending[rid] = q
            payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
            self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        try:
            resp = q.get(timeout=timeout)
        finally:
            with self._lock:
                self._pending.pop(rid, None)

        if "error" in resp and resp["error"]:
            err = resp["error"]
            raise RuntimeError(f"MCP error {err.get('code')}: {err.get('message')}")
        return resp.get("result") or {}

    def call_tool(self, tool_name: str, arguments: Dict[str, Any], timeout: float = 45.0) -> Dict[str, Any]:
        t0 = time.time()
        if not self.connected or not self.proc or self.proc.poll() is not None:
            if not self.connect():
                return {"success": False, "error": f"No se pudo conectar al servidor MCP '{self.name}': {self.last_error}"}
        try:
            result = self._request("tools/call", {"name": tool_name, "arguments": arguments or {}}, timeout=timeout)
            is_err = bool(result.get("isError", False))
            content_blocks = result.get("content") or []
            text_parts = [b.get("text", "") for b in content_blocks if isinstance(b, dict) and b.get("type") == "text"]
            return {
                "success": not is_err,
                "server": self.name,
                "tool": tool_name,
                "output": "\n".join(text_parts) if text_parts else result,
                "raw_content": content_blocks,
                "elapsed_ms": round((time.time() - t0) * 1000, 2),
            }
        except Exception as e:
            return {
                "success": False,
                "server": self.name,
                "tool": tool_name,
                "error": str(e),
                "elapsed_ms": round((time.time() - t0) * 1000, 2),
            }

    def close(self):
        self.connected = False
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None


class HttpMCPConnection:
    """Conexión JSON-RPC 2.0 sobre HTTP hacia un servidor MCP externo."""

    def __init__(self, name: str, url: str, headers: Optional[Dict[str, str]] = None):
        self.name = name
        self.url = url.strip()
        self.headers = headers or {}
        self._req_id = 0
        self.tools: List[Dict[str, Any]] = []
        self.server_info: Dict[str, Any] = {}
        self.connected = False
        self.last_error = ""

    def _post_rpc(self, method: str, params: Dict[str, Any], timeout: float = 20.0) -> Dict[str, Any]:
        self._req_id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._req_id, "method": method, "params": params}).encode("utf-8")
        hdrs = {"Content-Type": "application/json", "Accept": "application/json", **self.headers}
        req = urllib.request.Request(self.url, data=body, headers=hdrs, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        if "error" in data and data["error"]:
            err = data["error"]
            raise RuntimeError(f"MCP HTTP error {err.get('code')}: {err.get('message')}")
        return data.get("result") or {}

    def connect(self, timeout: float = 15.0) -> bool:
        try:
            init_res = self._post_rpc(
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "LLMAgent", "version": "2.0.0"},
                },
                timeout=timeout,
            )
            self.server_info = init_res.get("serverInfo") or {}
            tools_res = self._post_rpc("tools/list", {}, timeout=timeout)
            self.tools = tools_res.get("tools") or []
            self.connected = True
            self.last_error = ""
            return True
        except Exception as e:
            self.last_error = str(e)
            self.connected = False
            return False

    def call_tool(self, tool_name: str, arguments: Dict[str, Any], timeout: float = 45.0) -> Dict[str, Any]:
        t0 = time.time()
        try:
            result = self._post_rpc("tools/call", {"name": tool_name, "arguments": arguments or {}}, timeout=timeout)
            is_err = bool(result.get("isError", False))
            content_blocks = result.get("content") or []
            text_parts = [b.get("text", "") for b in content_blocks if isinstance(b, dict) and b.get("type") == "text"]
            return {
                "success": not is_err,
                "server": self.name,
                "tool": tool_name,
                "output": "\n".join(text_parts) if text_parts else result,
                "elapsed_ms": round((time.time() - t0) * 1000, 2),
            }
        except Exception as e:
            return {"success": False, "server": self.name, "tool": tool_name, "error": str(e), "elapsed_ms": round((time.time() - t0) * 1000, 2)}

    def close(self):
        self.connected = False


class MCPClientManager:
    """Gestor de múltiples servidores MCP externos (stdio y HTTP) definidos en mcp_servers.json."""

    def __init__(self, config_path: Path = MCP_CONFIG_PATH):
        self.config_path = config_path
        self.connections: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def load_config(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            return {"mcpServers": {}}
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("mcpServers"), dict):
                return data
        except Exception:
            pass
        return {"mcpServers": {}}

    def save_config(self, cfg: Dict[str, Any]):
        self.config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    def reload_servers(self) -> List[Dict[str, Any]]:
        """Desconecta conexiones previas y conecta todos los servidores habilitados en mcp_servers.json."""
        with self._lock:
            for conn in self.connections.values():
                conn.close()
            self.connections.clear()

            cfg = self.load_config()
            for name, spec in cfg.get("mcpServers", {}).items():
                if not isinstance(spec, dict) or not spec.get("enabled", True):
                    continue
                if spec.get("url"):
                    conn = HttpMCPConnection(name, spec["url"], headers=spec.get("headers"))
                elif spec.get("command"):
                    conn = StdioMCPConnection(
                        name,
                        spec["command"],
                        args=spec.get("args") or [],
                        env=spec.get("env") or {},
                        cwd=spec.get("cwd"),
                    )
                else:
                    continue
                conn.connect(timeout=12.0)
                self.connections[name] = conn
        return self.get_status()

    def get_status(self) -> List[Dict[str, Any]]:
        out = []
        for name, conn in self.connections.items():
            out.append({
                "name": name,
                "transport": "http" if isinstance(conn, HttpMCPConnection) else "stdio",
                "connected": conn.connected,
                "server_info": conn.server_info,
                "tools": [t.get("name") for t in conn.tools],
                "error": conn.last_error,
            })
        return out

    def list_external_openai_tools(self) -> List[Dict[str, Any]]:
        """Expone las herramientas descubiertas en servidores MCP externos en formato OpenAI Function Calling."""
        out = []
        for srv_name, conn in self.connections.items():
            if not conn.connected:
                continue
            for t in conn.tools:
                raw_name = t.get("name", "")
                if not raw_name:
                    continue
                out.append({
                    "type": "function",
                    "function": {
                        "name": f"mcp__{srv_name}__{raw_name}",
                        "description": f"[MCP:{srv_name}] {t.get('description', '')}",
                        "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
                    },
                })
        return out

    def is_external_tool(self, tool_name: str) -> bool:
        return isinstance(tool_name, str) and tool_name.startswith("mcp__") and tool_name.count("__") >= 2

    def call_external_tool(self, namespaced_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        parts = namespaced_name.split("__", 2)
        if len(parts) != 3:
            return {"success": False, "error": f"Nombre de herramienta MCP inválido: {namespaced_name}"}
        _, srv_name, raw_tool = parts
        conn = self.connections.get(srv_name)
        if not conn:
            return {"success": False, "error": f"Servidor MCP '{srv_name}' no está conectado."}
        return conn.call_tool(raw_tool, arguments)

    def close_all(self):
        with self._lock:
            for conn in self.connections.values():
                conn.close()
            self.connections.clear()


mcp_client_manager = MCPClientManager()
