"""
Utilidades de red SSL-safe y descarga con barra de progreso y soporte de reanudación.
"""

import json
import ssl
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def build_ssl_context(strict: bool = True) -> ssl.SSLContext:
    if not strict:
        return ssl._create_unverified_context()
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def http_get_json(url: str, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=build_ssl_context(True)) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        if "ssl" not in str(e).lower() and "certificate" not in str(e).lower():
            raise
        with urllib.request.urlopen(req, timeout=timeout, context=build_ssl_context(False)) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))


def download_file_with_progress(url: str, dest: Path, label: Optional[str] = None, timeout: float = 60.0) -> Path:
    """Descarga un archivo grande en streaming a `.part` con barra de progreso y reanudación."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part_path = dest.with_suffix(dest.suffix + ".part")
    existing_size = part_path.stat().st_size if part_path.exists() else 0

    headers = {"User-Agent": DEFAULT_UA}
    if existing_size > 0:
        headers["Range"] = f"bytes={existing_size}-"

    req = urllib.request.Request(url, headers=headers)
    title = label or dest.name
    print(f"\n[*] Descargando {title} ...")

    def _run_stream(ctx: ssl.SSLContext):
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            status = int(getattr(resp, "status", 200))
            append_mode = (status == 206 and existing_size > 0)
            cl = resp.headers.get("Content-Length")
            rem_bytes = int(cl) if (cl and cl.isdigit()) else 0
            total_bytes = (existing_size + rem_bytes) if append_mode else rem_bytes
            downloaded = existing_size if append_mode else 0
            t0 = time.time()
            mode = "ab" if append_mode else "wb"

            with open(part_path, mode) as f_out:
                while True:
                    chunk = resp.read(262144)
                    if not chunk:
                        break
                    f_out.write(chunk)
                    downloaded += len(chunk)
                    elapsed = max(0.05, time.time() - t0)
                    speed_mb = ((downloaded - (existing_size if append_mode else 0)) / (1024 * 1024)) / elapsed
                    mb_down = downloaded / (1024 * 1024)
                    if total_bytes > 0:
                        mb_tot = total_bytes / (1024 * 1024)
                        pct = min(100.0, (downloaded * 100.0) / total_bytes)
                        filled = int(pct // 2.5)
                        bar = "=" * filled + "-" * (40 - filled)
                        sys.stdout.write(f"\r[{bar}] {pct:5.1f}% ({mb_down:.1f}/{mb_tot:.1f} MB) · {speed_mb:.1f} MB/s   ")
                    else:
                        sys.stdout.write(f"\rDescargado: {mb_down:.1f} MB · {speed_mb:.1f} MB/s   ")
                    sys.stdout.flush()

    try:
        _run_stream(build_ssl_context(True))
    except Exception as e:
        if "ssl" not in str(e).lower() and "certificate" not in str(e).lower():
            raise
        _run_stream(build_ssl_context(False))

    if dest.exists():
        dest.unlink()
    part_path.replace(dest)
    print(f"\n[OK] Guardado en: {dest}")
    return dest
