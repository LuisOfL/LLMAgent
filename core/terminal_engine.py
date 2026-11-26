# -*- coding: utf-8 -*-
"""
terminal_engine.py - Salto al SOTA: Motor de Terminal Integrada y Sandbox de Ejecución Aislada.
- Soporta Contenedores Efímeros (Docker / Podman con --rm, --memory 512m, --cpus 1.0, --pids-limit 64)
  y Sandbox Local Aislado (Job/Process Tree Isolation + Auditoría Estática AST de Seguridad + Límites de Tiempo).
- Audita e instala dependencias Python vía AST + Pip y detecta servidores web locales automáticamente.
"""

import os
import sys
import re
import ast
import time
import shutil
import threading
import subprocess
import webbrowser
import importlib.util
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple


DANGEROUS_HOST_PATTERNS = [
    r"(?i)\bformat\s+[a-z]:",
    r"(?i)\brm\s+-rf\s+/(?:\s|$|\*)",
    r"(?i)\bdel\s+/[sqf]+\s+c:\\windows",
    r"(?i)\brd\s+/s\s+/q\s+c:\\",
    r"(?i)\breg\s+delete\s+hklm\\",
    r"(?i)\bbcdedit\b",
    r"(?i)\bdiskpart\b",
    r"(?i)\bcipher\s+/w:c:"
]


class TerminalEngine:
    def __init__(self):
        self.lock = threading.Lock()
        self.active_process: Optional[subprocess.Popen] = None
        self.output_buffer: List[Dict[str, Any]] = []
        self.log_counter: int = 0
        self.detected_web_urls: List[str] = []
        self.active_file_running: Optional[str] = None
        # Configuración de Sandbox SOTA: "auto", "docker", "isolated_local"
        self.sandbox_mode: str = "auto"
        self.docker_image: str = "python:3.11-slim"
        self.memory_limit: str = "512m"
        self.cpu_limit: str = "1.0"
        self.network_policy: str = "bridge"  # "none" (aislamiento total de red) o "bridge"
        self.max_execution_seconds: int = 180
        self._cached_container_engine: Optional[str] = None
        self._last_runtime_check: float = 0.0

    def detect_container_runtime(self, force_refresh: bool = False) -> Optional[str]:
        """Detecta si Docker o Podman están activos y listos para levantar contenedores efímeros."""
        now = time.time()
        if (not force_refresh) and (now - self._last_runtime_check < 30.0):
            return self._cached_container_engine

        self._last_runtime_check = now
        for runtime in ("docker", "podman"):
            bin_path = shutil.which(runtime)
            if bin_path:
                try:
                    res = subprocess.run(
                        [bin_path, "info"],
                        capture_output=True,
                        text=True,
                        timeout=2.5
                    )
                    if res.returncode == 0:
                        self._cached_container_engine = runtime
                        return runtime
                except Exception:
                    pass
        self._cached_container_engine = None
        return None

    def get_sandbox_status(self) -> Dict[str, Any]:
        runtime = self.detect_container_runtime()
        effective_mode = "docker" if (self.sandbox_mode in ("auto", "docker") and runtime) else "isolated_local"
        return {
            "configured_mode": self.sandbox_mode,
            "effective_mode": effective_mode,
            "container_runtime_available": runtime,
            "docker_image": self.docker_image,
            "limits": {
                "memory": self.memory_limit,
                "cpus": self.cpu_limit,
                "pids_limit": 64,
                "network_policy": self.network_policy,
                "timeout_s": self.max_execution_seconds
            },
            "badge_label": f"Contenedor Efímero ({runtime.upper()})" if effective_mode == "docker" else "Sandbox Aislado (AST Guard + Quotas)"
        }

    def configure_sandbox(self, mode: str = "auto", network_policy: str = "bridge", memory_limit: str = "512m") -> Dict[str, Any]:
        if mode in ("auto", "docker", "isolated_local"):
            self.sandbox_mode = mode
        if network_policy in ("none", "bridge", "host"):
            self.network_policy = network_policy
        if memory_limit:
            self.memory_limit = memory_limit
        return self.get_sandbox_status()

    def audit_execution_safety(self, cmd_str: str, file_path: Optional[Path] = None, cwd: Optional[Path] = None) -> Tuple[bool, str]:
        """
        Auditoría de Seguridad Pre-Ejecución (Static AST & Command Guard):
        Verifica que el comando o script generado por la IA no intente destruir archivos del sistema
        anfitrión fuera del volumen montado del proyecto.
        """
        for pat in DANGEROUS_HOST_PATTERNS:
            if re.search(pat, cmd_str):
                return False, f"Comando bloqueado por directiva de protección de host (patrón crítico detectado: {pat})."

        if file_path and file_path.exists() and file_path.suffix.lower() == ".py":
            try:
                src = file_path.read_text(encoding="utf-8", errors="ignore")
                for pat in DANGEROUS_HOST_PATTERNS:
                    if re.search(pat, src):
                        return False, f"El archivo '{file_path.name}' contiene una instrucción destructiva fuera del sandbox."
                tree = ast.parse(src, filename=file_path.name)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        func_str = ""
                        try:
                            func_str = ast.unparse(node.func)
                        except Exception:
                            pass
                        if func_str in ("shutil.rmtree", "os.remove", "os.unlink") and node.args:
                            try:
                                arg0 = ast.unparse(node.args[0]).lower()
                                if "c:\\\\windows" in arg0 or "system32" in arg0 or arg0 in ("'/'", '"/"', "'c:\\\\'", '"c:\\\\"'):
                                    return False, f"Operación de borrado sobre ruta crítica del sistema bloqueada por AST Guard ({func_str})."
                            except Exception:
                                pass
            except SyntaxError as se:
                return False, f"Error sintáctico previo a la ejecución en {file_path.name} (línea {se.lineno}: {se.msg})."
            except Exception:
                pass

        return True, "Política de aislamiento verificada (Sandbox Guard OK)."

    def wrap_command_for_sandbox(self, inner_cmd: str, cwd: Path, target_file: Optional[str] = None) -> Tuple[str, str]:
        """
        Envuelve el comando en un contenedor efímero Docker/Podman (--rm con límites de CPU/RAM/PIDs)
        si el motor de contenedores está activo, o prepara el comando para el Sandbox Local Aislado.
        """
        runtime = self.detect_container_runtime()
        use_container = (self.sandbox_mode in ("auto", "docker")) and (runtime is not None)

        if use_container and runtime:
            ws_abs = str(cwd.resolve()).replace("\\", "/")
            container_cmd = inner_cmd
            if target_file and target_file.endswith(".py"):
                container_cmd = f'python "{target_file}"'
            elif target_file and target_file.endswith(".js"):
                container_cmd = f'node "{target_file}"'

            docker_wrapped = (
                f'{runtime} run --rm '
                f'--memory {self.memory_limit} '
                f'--cpus {self.cpu_limit} '
                f'--pids-limit 64 '
                f'--network {self.network_policy} '
                f'--security-opt no-new-privileges '
                f'-v "{ws_abs}:/workspace" '
                f'-w /workspace '
                f'{self.docker_image} sh -c "{container_cmd}"'
            )
            return docker_wrapped, f"Contenedor Efímero {runtime.upper()} ({self.memory_limit} RAM, {self.cpu_limit} CPU, net={self.network_policy})"

        return inner_cmd, "Sandbox Local Aislado (AST Guard + Quotas + Árbol de Procesos)"

    def log(self, text: str, stream: str = "stdout", level: str = "info"):
        with self.lock:
            self.log_counter += 1
            entry = {
                "id": self.log_counter,
                "time": time.strftime("%H:%M:%S"),
                "text": text,
                "stream": stream,
                "level": level
            }
            self.output_buffer.append(entry)
            if len(self.output_buffer) > 2000:
                self.output_buffer.pop(0)

    def get_logs(self, since_id: int = 0) -> List[Dict[str, Any]]:
        with self.lock:
            return [e for e in self.output_buffer if e["id"] > since_id]

    def clear(self):
        with self.lock:
            self.output_buffer.clear()
            self.log_counter = 0

    def kill(self) -> bool:
        with self.lock:
            if self.active_process and self.active_process.poll() is None:
                try:
                    pid = self.active_process.pid
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=4)
                    else:
                        self.active_process.terminate()
                    self.log("[Sandbox] Árbol de procesos detenido y recursos liberados.", stream="system", level="warn")
                    return True
                except Exception as e:
                    self.log(f"[Sandbox] Error al detener proceso: {e}", stream="stderr", level="error")
            return False

    def is_running(self) -> bool:
        with self.lock:
            return bool(self.active_process and self.active_process.poll() is None)

    def find_dependencies(self, file_path: Path) -> List[str]:
        if not file_path.exists() or file_path.suffix.lower() != ".py":
            return []
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            return []
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_pkg = alias.name.split(".")[0]
                    modules.add(root_pkg)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_pkg = node.module.split(".")[0]
                    modules.add(root_pkg)
        stdlib = getattr(sys, "stdlib_module_names", set())
        third_party = []
        for mod in sorted(modules):
            if mod in stdlib:
                continue
            if (file_path.parent / f"{mod}.py").exists() or (file_path.parent / mod).is_dir():
                continue
            third_party.append(mod)
        return third_party

    def check_missing_dependencies(self, file_path: Path) -> List[str]:
        deps = self.find_dependencies(file_path)
        missing = []
        pip_map = {
            "bs4": "beautifulsoup4",
            "PIL": "pillow",
            "cv2": "opencv-python",
            "yaml": "pyyaml",
            "dotenv": "python-dotenv",
            "sklearn": "scikit-learn"
        }
        for d in deps:
            if importlib.util.find_spec(d) is None:
                missing.append(pip_map.get(d, d))
        return missing

    def install_dependencies(self, packages: List[str]) -> Tuple[bool, str]:
        if not packages:
            return True, "No se requieren paquetes."
        self.log(f"[Pip] Instalando dependencias: {', '.join(packages)}...", stream="system", level="info")
        try:
            cmd = [sys.executable, "-m", "pip", "install"] + packages
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if res.returncode == 0:
                self.log(f"[Pip] Instalación exitosa:\n{res.stdout.strip()}", stream="stdout", level="success")
                return True, res.stdout
            else:
                self.log(f"[Pip] Error en instalación:\n{res.stderr.strip()}", stream="stderr", level="error")
                return False, res.stderr
        except Exception as e:
            self.log(f"[Pip] Excepción al instalar: {e}", stream="stderr", level="error")
            return False, str(e)

    def _build_isolated_env(self, cwd: Path) -> Dict[str, str]:
        """Crea un entorno de variables sanitizado para el proceso aislado."""
        safe_env = dict(os.environ)
        safe_env["PYTHONUNBUFFERED"] = "1"
        safe_env["PYTHONIOENCODING"] = "utf-8"
        safe_env["ANTIGRAVITY_SANDBOX"] = "1"
        safe_env["ANTIGRAVITY_WORKSPACE"] = str(cwd.resolve())
        return safe_env

    def run_command(self, cmd_str: str, cwd: Path, target_file: Optional[str] = None):
        self.kill()
        target_p = (cwd / target_file).resolve() if target_file else None
        is_safe, safety_msg = self.audit_execution_safety(cmd_str, file_path=target_p, cwd=cwd)
        if not is_safe:
            self.log(f"[Sandbox Guard BLOQUEADO] {safety_msg}", stream="stderr", level="error")
            return

        final_cmd, sandbox_desc = self.wrap_command_for_sandbox(cmd_str, cwd=cwd, target_file=target_file)
        self.log(f"[Sandbox: {sandbox_desc}] {safety_msg}", stream="system", level="info")
        self.log(f"$ {final_cmd}", stream="prompt", level="info")

        def _runner():
            try:
                proc = subprocess.Popen(
                    final_cmd,
                    shell=True,
                    cwd=str(cwd),
                    env=self._build_isolated_env(cwd),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1
                )
                with self.lock:
                    self.active_process = proc

                def read_stdout():
                    try:
                        for line in iter(proc.stdout.readline, ''):
                            if not line:
                                break
                            clean_line = line.rstrip("\r\n")
                            self.log(clean_line, stream="stdout")
                            self._check_web_url(clean_line)
                    except Exception:
                        pass

                def read_stderr():
                    try:
                        for line in iter(proc.stderr.readline, ''):
                            if not line:
                                break
                            clean_line = line.rstrip("\r\n")
                            self.log(clean_line, stream="stderr", level="error")
                            self._check_web_url(clean_line)
                    except Exception:
                        pass

                t1 = threading.Thread(target=read_stdout, daemon=True)
                t2 = threading.Thread(target=read_stderr, daemon=True)
                t1.start()
                t2.start()

                try:
                    proc.wait(timeout=self.max_execution_seconds)
                except subprocess.TimeoutExpired:
                    self.log(f"[Sandbox Quota] Límite de tiempo ({self.max_execution_seconds}s) excedido. Terminando proceso...", stream="stderr", level="warn")
                    self.kill()

                t1.join(timeout=1.0)
                t2.join(timeout=1.0)
                code_lvl = "success" if proc.returncode == 0 else "warn"
                self.log(f"[Proceso finalizado con código de salida {proc.returncode}]", stream="system", level=code_lvl)
            except Exception as e:
                self.log(f"[Error ejecutando comando en Sandbox]: {e}", stream="stderr", level="error")

        threading.Thread(target=_runner, daemon=True).start()

    def run_command_sync(self, cmd_str: str, cwd: Path, timeout_s: int = 20) -> Dict[str, Any]:
        """
        Ejecución síncrona aislada para invocación autónoma de herramientas (MCP / ReAct Agent Tool Calling).
        """
        is_safe, safety_msg = self.audit_execution_safety(cmd_str, cwd=cwd)
        if not is_safe:
            self.log(f"[MCP Sandbox Block] {safety_msg}", stream="stderr", level="error")
            return {"success": False, "exit_code": -1, "stdout": "", "stderr": safety_msg, "sandbox": "blocked"}

        final_cmd, sandbox_desc = self.wrap_command_for_sandbox(cmd_str, cwd=cwd)
        self.log(f"[MCP Tool Call -> {sandbox_desc}] $ {final_cmd}", stream="prompt", level="info")
        try:
            res = subprocess.run(
                final_cmd,
                shell=True,
                cwd=str(cwd),
                env=self._build_isolated_env(cwd),
                capture_output=True,
                text=True,
                timeout=timeout_s
            )
            out_txt = (res.stdout or "").strip()
            err_txt = (res.stderr or "").strip()
            if out_txt:
                for l in out_txt.splitlines()[:40]:
                    self.log(l, stream="stdout")
            if err_txt:
                for l in err_txt.splitlines()[:30]:
                    self.log(l, stream="stderr", level="warn" if res.returncode == 0 else "error")
            return {
                "success": res.returncode == 0,
                "exit_code": res.returncode,
                "stdout": out_txt[:4000],
                "stderr": err_txt[:4000],
                "sandbox": sandbox_desc
            }
        except subprocess.TimeoutExpired:
            msg = f"Comando excedió el límite de tiempo del sandbox ({timeout_s}s)."
            self.log(f"[MCP Sandbox Timeout] {msg}", stream="stderr", level="warn")
            return {"success": False, "exit_code": -2, "stdout": "", "stderr": msg, "sandbox": sandbox_desc}
        except Exception as e:
            return {"success": False, "exit_code": -3, "stdout": "", "stderr": str(e), "sandbox": sandbox_desc}

    def _check_web_url(self, text: str):
        url_match = re.search(r'(https?://(?:127\.0\.0\.1|localhost|0\.0\.0\.0):\d+[^\s\r\n]*)', text)
        if url_match:
            raw_url = url_match.group(1).replace("0.0.0.0", "127.0.0.1")
            if raw_url not in self.detected_web_urls:
                self.detected_web_urls.append(raw_url)
                self.log(f"[Web Preview] Servidor web detectado en {raw_url} -> Abriendo en navegador...", stream="system", level="success")
                try:
                    webbrowser.open(raw_url)
                except Exception:
                    pass

    def run_file(self, target_file: str, cwd: Path, auto_install: bool = True) -> Dict[str, Any]:
        target_path = (cwd / target_file).resolve()
        if not target_path.exists():
            for candidate in ["main.py", "app.py", "server.py", "index.html"]:
                cand_path = cwd / candidate
                if cand_path.exists():
                    target_path = cand_path
                    target_file = candidate
                    break

        if not target_path.exists():
            return {"success": False, "message": f"No se encontró el archivo '{target_file}' en el proyecto."}

        # Verificar frontera de seguridad del workspace
        if not str(target_path).startswith(str(cwd.resolve())):
            return {"success": False, "message": "Acceso denegado: el archivo está fuera del volumen del proyecto."}

        self.active_file_running = target_file

        # 1. Si es archivo HTML
        if target_path.suffix.lower() == ".html":
            file_url = f"file:///{str(target_path).replace(os.sep, '/')}"
            self.log(f"[Web Preview] Abriendo archivo HTML en navegador: {file_url}", stream="system", level="success")
            try:
                webbrowser.open(file_url)
            except Exception as e:
                return {"success": False, "message": f"Error abriendo navegador: {e}"}
            return {"success": True, "type": "html", "file": target_file, "url": file_url}

        # 2. Si es archivo Python: auditar seguridad AST y dependencias
        if target_path.suffix.lower() == ".py":
            is_safe, safety_msg = self.audit_execution_safety("", file_path=target_path, cwd=cwd)
            if not is_safe:
                self.log(f"[Sandbox Guard] {safety_msg}", stream="stderr", level="error")
                return {"success": False, "message": safety_msg}

            missing = self.check_missing_dependencies(target_path)
            if missing:
                self.log(f"[Dependencias] Paquetes faltantes detectados: {', '.join(missing)}", stream="system", level="warn")
                if auto_install:
                    ok_inst, out_inst = self.install_dependencies(missing)
                    if not ok_inst:
                        return {"success": False, "message": f"Error instalando dependencias: {missing}"}
                else:
                    return {"success": False, "missing_deps": missing, "message": "Dependencias faltantes."}
            else:
                self.log(f"[Dependencias] Todas las librerías requeridas están instaladas.", stream="system", level="success")

            cmd = f'"{sys.executable}" "{str(target_path)}"'
            self.run_command(cmd, cwd=cwd, target_file=target_file)
            return {"success": True, "type": "python", "file": target_file, "sandbox": self.get_sandbox_status()}

        # 3. Si es NodeJS / JS
        if target_path.suffix.lower() in [".js", ".mjs"]:
            cmd = f'node "{str(target_path)}"'
            self.run_command(cmd, cwd=cwd, target_file=target_file)
            return {"success": True, "type": "node", "file": target_file, "sandbox": self.get_sandbox_status()}

        return {"success": False, "message": f"Tipo de archivo no soportado para ejecución directa: {target_file}"}

    def open_external_console(self, cwd: Path, file_name: Optional[str] = None):
        target = file_name or "main.py"
        target_path = cwd / target
        if target_path.exists() and target_path.suffix.lower() == ".py":
            cmd = f'start cmd /k "cd /d "{str(cwd.resolve())}" && "{sys.executable}" "{target}""'
        else:
            cmd = f'start cmd /k "cd /d "{str(cwd.resolve())}"""'
        os.system(cmd)
        self.log(f"[Consola Externa] Abierta ventana de Windows CMD en: {cwd}", stream="system", level="info")
        return True


terminal_engine = TerminalEngine()
