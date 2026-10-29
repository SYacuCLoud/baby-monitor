"""Start H2O Lightning (GGUF Q4) for crib /v1/systemone. Not a medical device.

8GB GPU: load Q4_K_M (4-bit GGUF) via llama.cpp b11946+ native decision endpoint.
BF16 safetensors (~9.3GB) do not fit in 8GB VRAM; bitsandbytes is unused.
Quantization is printed at startup and written to frames/lightning_quant.txt.

llama.cpp build 11773+ answers POST /v1/systemone itself for this GGUF; the
official Python shim is not used (it calls /completion, which decision models refuse).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DIR = Path(__file__).resolve().parent
GGUF_DIR = Path(os.environ.get("LIGHTNING_GGUF_DIR", r"D:\Dev\_Models\h2o-lightning-4b-gguf"))
MODEL = Path(os.environ.get("LIGHTNING_GGUF", str(GGUF_DIR / "h2o-lightning-4b-Q4_K_M.gguf")))
MMPROJ = Path(os.environ.get("LIGHTNING_MMPROJ", str(GGUF_DIR / "mmproj-lightning-f16.gguf")))
LLAMA = Path(os.environ.get("LIGHTNING_LLAMA_SERVER", str(GGUF_DIR / "llama-b11946" / "llama-server.exe")))
PORT = os.environ.get("CRIB_LIGHTNING_PORT", "8741")
QUANT = "Q4_K_M (GGUF 4-bit, llama.cpp b11946 native /v1/systemone)"
CONTEXT = int(os.environ.get("CRIB_LIGHTNING_CONTEXT", "8192"))


def _write_quant_note() -> None:
    note = DIR / "frames" / "lightning_quant.txt"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        f"model={MODEL}\nquant={QUANT}\ncontext={CONTEXT}\nport={PORT}\n"
        "note=BF16 safetensors need ~9.3GB; using official GGUF Q4_K_M for 8GB GPU\n"
        "backend=llama.cpp native decision model (no Python shim)\n",
        encoding="utf-8",
    )
    print(f"[lightning] quantization: {QUANT}", flush=True)
    print(f"[lightning] model: {MODEL}", flush=True)
    print(f"[lightning] note written: {note}", flush=True)


def wait_http(url: str, proc: subprocess.Popen, timeout: float = 300.0) -> None:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"process exited {proc.returncode} before ready: {url}")
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:
            last = exc
        time.sleep(1)
    raise RuntimeError(f"timeout waiting for {url}: {last}")


def main() -> int:
    _write_quant_note()
    for path, label in (
        (MODEL, "Q4_K_M GGUF"),
        (MMPROJ, "mmproj"),
        (LLAMA, "llama-server"),
    ):
        if not path.is_file():
            print(f"[lightning] missing {label}: {path}", file=sys.stderr, flush=True)
            return 1

    log_dir = DIR / "frames" / "lightning-llamacpp-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(LLAMA),
        "-m", str(MODEL.resolve()),
        "--alias", "h2oai/h2o-lightning-4b",
        "-c", str(CONTEXT),
        "-np", "1",
        "--no-kv-unified",
        "-ngl", "99",
        "--threads", "8",
        "--host", "127.0.0.1",
        "--port", str(PORT),
        "--mmproj", str(MMPROJ.resolve()),
        "--image-max-tokens", "1024",
    ]
    print(f"[lightning] starting: {' '.join(cmd)}", flush=True)
    backend_log = open(log_dir / "backend.log", "ab", buffering=0)
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = os.environ.copy()
    proc = subprocess.Popen(
        cmd, env=env, stdout=backend_log, stderr=subprocess.STDOUT, creationflags=flags
    )
    print("[lightning] waiting for /health", flush=True)
    wait_http(f"http://127.0.0.1:{PORT}/health", proc)
    print(f"[lightning] Ready: http://127.0.0.1:{PORT}/v1/systemone quant={QUANT}", flush=True)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
