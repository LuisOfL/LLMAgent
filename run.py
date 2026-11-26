"""
Lanzador principal de LLMAgent.
Verifica dependencias, libera el puerto 7860, inicia FastAPI y abre el navegador.
"""

import os
import sys
import time
import subprocess
import webbrowser
import threading
import importlib.util
from pathlib import Path


def ensure_dependencies():
    """Instala automáticamente requirements.txt si falta alguna librería esencial en el entorno Python."""
    required_modules = ["uvicorn", "fastapi", "pydantic", "psutil", "numpy", "networkx"]
    missing = [mod for mod in required_modules if importlib.util.find_spec(mod) is None]
    if missing:
        print(f"[*] Faltan dependencias ({', '.join(missing)}). Instalando automáticamente desde requirements.txt...")
        req_file = Path(__file__).resolve().parent / "requirements.txt"
        if req_file.exists():
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(req_file)])
        else:
            subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
        print("[OK] Dependencias instaladas correctamente.\n")


def free_port_if_in_use(port: int):
    """Libera el puerto si quedó un proceso zombie de una ejecución anterior."""
    import psutil
    current_pid = os.getpid()
    for conn in psutil.net_connections(kind="inet"):
        if conn.laddr and conn.laddr.port == port and conn.status == "LISTEN" and conn.pid and conn.pid != current_pid:
            try:
                proc = psutil.Process(conn.pid)
                print(f"[*] Liberando puerto {port} ocupado por proceso previo (PID {conn.pid}: {proc.name()})...")
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    psutil.Process(conn.pid).kill()
                except Exception:
                    pass


def open_browser():
    time.sleep(1.2)
    url = "http://127.0.0.1:7860"
    print(f"\n[+] Abriendo entorno gráfico en: {url}")
    webbrowser.open(url)


def cleanup_local_engine_processes():
    """Cierra cualquier llama-server.exe lanzado desde esta carpeta para que nunca bloquee archivos al salir."""
    try:
        import psutil
        base_str = str(Path(__file__).resolve().parent).lower()
        for proc in psutil.process_iter(["name", "exe", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                exe = (proc.info.get("exe") or "").lower()
                cmd = " ".join(proc.info.get("cmdline") or []).lower()
                if "llama-server" in name and (base_str in exe or base_str in cmd):
                    proc.terminate()
                    proc.wait(timeout=3)
            except Exception:
                pass
    except Exception:
        pass


if __name__ == "__main__":
    print("\n" + "=" * 65)
    print("      LLMAGENT - ENTORNO GGUF & AGENTES SOTA")
    print("      Auto-optimizado para cualquier PC (GPU NVIDIA o CPU)")
    print("=" * 65 + "\n")

    ensure_dependencies()
    free_port_if_in_use(7860)

    import uvicorn

    print("[*] Iniciando servidor de inferencia y telemetría...")
    threading.Thread(target=open_browser, daemon=True).start()
    try:
        uvicorn.run("server:app", host="127.0.0.1", port=7860, reload=False, log_level="warning")
    finally:
        cleanup_local_engine_processes()
