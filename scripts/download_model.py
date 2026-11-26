"""
Descargador CLI interactivo de modelos GGUF para LLMAgent.
Reutiliza `utils.net_utils` con barra de progreso, SSL seguro y reanudación.
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"
MODELS_DIR.mkdir(exist_ok=True)

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from utils.net_utils import download_file_with_progress

PRESETS = {
    "1": {
        "name": "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        "info": "4.68 GB · Especialista en código y apps (recomendado 6 GB VRAM)",
        "url": "https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/qwen2.5-coder-7b-instruct-q4_k_m.gguf",
    },
    "2": {
        "name": "mlabonne_Qwen3-8B-abliterated-IQ4_XS.gguf",
        "info": "4.28 GB · 100% en 6 GB VRAM, rápido y sin censura",
        "url": "https://huggingface.co/bartowski/mlabonne_Qwen3-8B-abliterated-GGUF/resolve/main/mlabonne_Qwen3-8B-abliterated-IQ4_XS.gguf",
    },
    "3": {
        "name": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "info": "2.02 GB · Ultra ligero y rápido (~80-100 t/s)",
        "url": "https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
    },
    "4": {
        "name": "Qwen2.5-Coder-14B-Instruct-abliterated-IQ4_XS.gguf",
        "info": "7.56 GB · Alta calidad 14B (híbrido GPU 6 GB + RAM)",
        "url": "https://huggingface.co/bartowski/Qwen2.5-Coder-14B-Instruct-abliterated-GGUF/resolve/main/Qwen2.5-Coder-14B-Instruct-abliterated-IQ4_XS.gguf",
    },
    "5": {
        "name": "qwen2.5-coder-0.5b-instruct-q4_k_m.gguf",
        "info": "0.39 GB · Acelerador Speculative Decoding para Qwen2.5 14B",
        "url": "https://huggingface.co/Qwen/Qwen2.5-Coder-0.5B-Instruct-GGUF/resolve/main/qwen2.5-coder-0.5b-instruct-q4_k_m.gguf",
    },
}


def main():
    print("=" * 64)
    print("  LLMAgent — Descargador CLI de Modelos GGUF")
    print("=" * 64)
    for k, p in PRESETS.items():
        print(f" [{k}] {p['name']}\n     {p['info']}")
    print(" [0] Salir")

    choice = input("\nElige una opción (1-5): ").strip()
    if choice not in PRESETS:
        print("Operación cancelada.")
        return

    preset = PRESETS[choice]
    dest = MODELS_DIR / preset["name"]
    if dest.exists():
        print(f"[OK] {preset['name']} ya existe en models/.")
        return

    download_file_with_progress(preset["url"], dest, label=preset["name"])


if __name__ == "__main__":
    main()
