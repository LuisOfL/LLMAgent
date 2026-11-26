"""
setup.py - Instalador todo-en-uno para LLMAgent.

Uso tras clonar el repositorio:
    python setup.py
    python run.py

Qué hace automáticamente:
1. Instala las dependencias Python desde requirements.txt.
2. Descarga el motor llama.cpp (llama-server + DLLs CUDA o CPU según tu PC) en engine/.
3. Descarga un modelo GGUF inicial en models/ si aún no tienes ninguno.

Opciones:
    python setup.py --cpu                 # Forzar binarios CPU (sin CUDA)
    python setup.py --cuda                # Forzar binarios CUDA 12.4 (GPU NVIDIA)
    python setup.py --model llama-3.2-3b  # Elegir modelo inicial específico
    python setup.py --no-model            # Omitir descarga de modelo (descargar luego desde la UI)
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENGINE_DIR = BASE_DIR / "engine"
MODELS_DIR = BASE_DIR / "models"
REQ_FILE = BASE_DIR / "requirements.txt"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from utils.net_utils import http_get_json, download_file_with_progress

STARTER_MODELS = {
    "qwen2.5-coder-7b": {
        "name": "Qwen 2.5 Coder 7B Instruct (Q4_K_M · 4.68 GB · Recomendado para código)",
        "filename": "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        "url": "https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/qwen2.5-coder-7b-instruct-q4_k_m.gguf",
    },
    "qwen3-8b": {
        "name": "Qwen 3 8B Abliterated (IQ4_XS · 4.28 GB · 100% en GPU 6GB)",
        "filename": "mlabonne_Qwen3-8B-abliterated-IQ4_XS.gguf",
        "url": "https://huggingface.co/bartowski/mlabonne_Qwen3-8B-abliterated-GGUF/resolve/main/mlabonne_Qwen3-8B-abliterated-IQ4_XS.gguf",
    },
    "llama-3.2-3b": {
        "name": "Llama 3.2 3B Instruct (Q4_K_M · 2.02 GB · Ligero y ultra rápido)",
        "filename": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "url": "https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
    },
    "qwen2.5-coder-14b": {
        "name": "Qwen 2.5 Coder 14B Abliterated (IQ4_XS · 7.56 GB · Alta calidad 14B)",
        "filename": "Qwen2.5-Coder-14B-Instruct-abliterated-IQ4_XS.gguf",
        "url": "https://huggingface.co/bartowski/Qwen2.5-Coder-14B-Instruct-abliterated-GGUF/resolve/main/Qwen2.5-Coder-14B-Instruct-abliterated-IQ4_XS.gguf",
    },
}


def has_nvidia_gpu() -> bool:
    try:
        res = subprocess.run(["nvidia-smi"], capture_output=True, timeout=5)
        return res.returncode == 0
    except Exception:
        return False


def step_install_requirements():
    print("\n=== [1/3] Instalando dependencias Python ===")
    if not REQ_FILE.exists():
        print("[!] No se encontró requirements.txt, omitiendo.")
        return
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(REQ_FILE)])
    print("[OK] Dependencias Python listas.")


def _extract_zip_flat_to_engine(zip_path: Path):
    """Extrae todos los binarios/DLLs de un zip de llama.cpp dentro de engine/ con permisos normales de usuario."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            fname = Path(member.filename).name
            if not fname:
                continue
            target = ENGINE_DIR / fname
            with zf.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            try:
                os.chmod(target, 0o755 if fname.endswith((".exe", ".dll", ".so", ".dylib")) else 0o644)
            except Exception:
                pass


def step_clean_installation():
    """Detiene procesos locales de llama-server y limpia binarios, modelos y cachés para poder borrar la carpeta normalmente."""
    print("\n=== Limpiando instalación local de LLMAgent ===")
    if platform.system().lower().startswith("win"):
        subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True)
    for rel in (".antigravity_rag", ".audit", "__pycache__", "core/__pycache__", "utils/__pycache__", "scripts/__pycache__"):
        p = BASE_DIR / rel
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    for d in (ENGINE_DIR, MODELS_DIR):
        if d.exists():
            for item in d.iterdir():
                if item.name == ".gitkeep":
                    continue
                try:
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        os.chmod(item, 0o666)
                        item.unlink()
                except Exception:
                    pass
    print("[OK] Procesos detenidos y archivos temporales/binarios limpiados. Ya puedes borrar la carpeta desde el Explorador.")


def step_install_llama_engine(force_mode: str = "auto"):
    print("\n=== [2/3] Verificando motor de inferencia llama.cpp (engine/) ===")
    ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    is_win = platform.system().lower().startswith("win")
    server_bin = ENGINE_DIR / ("llama-server.exe" if is_win else "llama-server")
    if server_bin.exists():
        print(f"[OK] Motor ya instalado: {server_bin}")
        return

    use_cuda = (force_mode == "cuda") or (force_mode == "auto" and is_win and has_nvidia_gpu())
    print(f"[*] Plataforma: {platform.system()} ({platform.machine()}) | Aceleración: {'NVIDIA CUDA 12.4' if use_cuda else 'CPU'}")
    print("[*] Consultando última release oficial de ggml-org/llama.cpp ...")

    release = http_get_json("https://api.github.com/repos/ggml-org/llama.cpp/releases/latest", timeout=25.0)
    assets = release.get("assets") or []
    tag = release.get("tag_name", "latest")
    print(f"[*] Release encontrada: {tag}")

    to_download = []
    if is_win:
        if use_cuda:
            bin_asset = next((a for a in assets if "bin-win-cuda-12.4-x64.zip" in a.get("name", "").lower()), None)
            if not bin_asset:
                bin_asset = next((a for a in assets if "bin-win-cuda-cu12" in a.get("name", "").lower() and a.get("name", "").lower().endswith(".zip")), None)
            cudart_asset = next((a for a in assets if "cudart-llama-bin-win-cuda-12.4-x64.zip" in a.get("name", "").lower()), None)
            if not cudart_asset:
                cudart_asset = next((a for a in assets if "cudart" in a.get("name", "").lower() and "win" in a.get("name", "").lower() and "12" in a.get("name", "").lower() and a.get("name", "").lower().endswith(".zip")), None)
            if bin_asset:
                to_download.append(bin_asset)
            if cudart_asset:
                to_download.append(cudart_asset)
        if not to_download:
            cpu_asset = next((a for a in assets if "bin-win-cpu-x64.zip" in a.get("name", "").lower()), None)
            if cpu_asset:
                to_download.append(cpu_asset)
    elif platform.system().lower() == "darwin":
        mac_asset = next((a for a in assets if "bin-macos-arm64.zip" in a.get("name", "").lower()), None)
        if mac_asset:
            to_download.append(mac_asset)
    else:
        lin_asset = next((a for a in assets if "bin-ubuntu-x64.zip" in a.get("name", "").lower()), None)
        if lin_asset:
            to_download.append(lin_asset)

    if not to_download:
        print("[!] No se encontró un paquete precompilado compatible en GitHub Releases.")
        print("    Descárgalo manualmente desde https://github.com/ggml-org/llama.cpp/releases y descomprímelo en engine/.")
        return

    tmp_dir = ENGINE_DIR / "_tmp_setup"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        for asset in to_download:
            a_name = asset["name"]
            a_url = asset["browser_download_url"]
            zip_file = tmp_dir / a_name
            download_file_with_progress(a_url, zip_file, label=a_name)
            print(f"[*] Extrayendo {a_name} en engine/ ...")
            _extract_zip_flat_to_engine(zip_file)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if not is_win and server_bin.exists():
        os.chmod(server_bin, 0o755)
    print(f"[OK] Motor llama.cpp listo en: {ENGINE_DIR}")


def step_install_starter_model(model_key: str = "qwen2.5-coder-7b", skip_model: bool = False):
    print("\n=== [3/3] Verificando modelo GGUF inicial (models/) ===")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    if skip_model:
        print("[*] Descarga de modelo omitida (--no-model). Puedes descargar modelos luego desde el botón 'Modelos' en la UI.")
        return

    existing = [f for f in MODELS_DIR.glob("*.gguf") if "mmproj" not in f.name.lower()]
    if existing:
        print(f"[OK] Ya tienes {len(existing)} modelo(s) GGUF instalados: {', '.join(f.name for f in existing)}")
        return

    chosen = STARTER_MODELS.get(model_key, STARTER_MODELS["qwen2.5-coder-7b"])
    print(f"[*] Modelo seleccionado: {chosen['name']}")
    dest = MODELS_DIR / chosen["filename"]
    download_file_with_progress(chosen["url"], dest, label=chosen["filename"])
    print("[OK] Modelo inicial listo.")


def main():
    parser = argparse.ArgumentParser(description="Instalador automático de LLMAgent (dependencias + llama.cpp + modelo GGUF).")
    parser.add_argument("--cpu", action="store_true", help="Forzar descarga del motor llama.cpp solo CPU.")
    parser.add_argument("--cuda", action="store_true", help="Forzar descarga del motor llama.cpp con CUDA 12.4.")
    parser.add_argument("--model", choices=list(STARTER_MODELS.keys()), default="qwen2.5-coder-7b", help="Modelo GGUF inicial a descargar.")
    parser.add_argument("--no-model", action="store_true", help="No descargar modelo ahora (se puede descargar desde la UI).")
    parser.add_argument("--clean", action="store_true", help="Detiene llama-server y limpia engine/, models/ y cachés para desinstalar.")
    args = parser.parse_args()

    if args.clean:
        step_clean_installation()
        return

    print("=" * 68)
    print("  LLMAGENT — INSTALADOR AUTOMÁTICO PARA GITHUB")
    print("=" * 68)

    force_mode = "cpu" if args.cpu else ("cuda" if args.cuda else "auto")
    step_install_requirements()
    step_install_llama_engine(force_mode=force_mode)
    step_install_starter_model(model_key=args.model, skip_model=args.no_model)

    print("\n" + "=" * 68)
    print("  [LISTO] Instalación completada.")
    print("  Inicia la aplicación ejecutando:  python run.py")
    print("=" * 68 + "\n")


if __name__ == "__main__":
    main()
