"""Start a Jev server on Google Colab and expose it through a token-checking gateway and a
Cloudflare quick tunnel. Not a medical device. NOT verified end to end by the author (the individual
steps were run by hand on a Colab T4, this script as a whole was not).

Use from the notebook (colab/imajev_colab.ipynb) or in a Colab cell:
    import start_colab as sc
    sc.install("4b")          # clone imajev, pip install, download model + adapter (slow, once)
    state = sc.launch("4b")   # server + gateway + tunnel, prints URL and token for the PC
    sc.health_loop(state)     # status line every 30 s; shows when the tunnel dies

Steps follow what worked by hand on a Colab T4 (Python 3.13, transformers 5.17, torch 2.11):
  git clone imajev; pip install -e ".[serve,torch]"; pip install -U torchao (torchao 0.10 was incompatible);
  python scripts/download_model.py --model {2b|4b}; hf download mohit67890/imajev-{2b|4b} (no mlx/*);
  server started with subprocess.Popen from Python (a shell `nohup ... &` died when the cell ended);
  no --merge-lora (12 GB host RAM) and no --fast.
The token is made here (secrets.token_urlsafe(32)), passed to the gateway through the environment (not argv),
and printed only in the banner. Nothing is written to the repo.

  python colab/start_colab.py --selftest      offline checks of the pure parts
"""

from __future__ import annotations

import http.client
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = Path("/content") if Path("/content").is_dir() else Path.cwd()
IMAJEV_URL = "https://github.com/mohit67890/imajev"
CLOUDFLARED_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
MODEL_PORT = 8090
GATEWAY_PORT = 8091
MODELS = ("2b", "4b")
TUNNEL_RE = re.compile(r"https://([a-z0-9][a-z0-9-]*)\.trycloudflare\.com")


def imajev_dir() -> Path:
    return WORK / "imajev"


def _check_model(model: str) -> str:
    m = (model or "").strip().lower()
    if m not in MODELS:
        raise SystemExit("MODEL must be '2b' or '4b'")
    return m


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> None:
    """Run a setup step, stream its output, stop on failure."""
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=True)


def check_gpu() -> None:
    if shutil.which("nvidia-smi"):
        subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], check=False)
    else:
        print("!! GPU가 보이지 않습니다. 런타임 > 런타임 유형 변경 > T4 GPU 를 고르세요.", flush=True)


def install(model: str) -> None:
    """Clone imajev, install it, download the base model and the adapter. Safe to run again."""
    model = _check_model(model)
    check_gpu()
    d = imajev_dir()
    if not d.is_dir():
        run(["git", "clone", "--depth", "1", IMAJEV_URL, str(d)])
    run([sys.executable, "-m", "pip", "install", "-q", "-e", ".[serve,torch]"], cwd=d)
    run([sys.executable, "-m", "pip", "install", "-q", "-U", "torchao"])  # torchao 0.10 was incompatible
    run([sys.executable, "scripts/download_model.py", "--model", model], cwd=d)
    adapter = d / "adapters" / f"imajev-{model}"
    if not (adapter / "calibration.json").is_file():
        hf = shutil.which("hf") or shutil.which("huggingface-cli")
        if not hf:
            raise SystemExit("hf CLI not found after pip install (huggingface_hub)")
        run([hf, "download", f"mohit67890/imajev-{model}", "--local-dir", f"adapters/imajev-{model}",
             "--exclude", "mlx/*"], cwd=d)
    if not (adapter / "calibration.json").is_file():
        raise SystemExit(f"adapter download incomplete: {adapter}/calibration.json missing")
    print(f"install done ({model})", flush=True)


def server_argv(model: str) -> list[str]:
    """The imajev server command. 4b needs its bundle; 2b uses the default bundle. No --merge-lora, no --fast."""
    model = _check_model(model)
    argv = [sys.executable, "scripts/playground/server.py", "--backend", "torch"]
    if model == "4b":
        argv += ["--model-bundle", "artifacts/model-qwen4b.json"]
    argv += ["--adapter", f"adapters/imajev-{model}",
             "--calibration", f"adapters/imajev-{model}/calibration.json",
             "--model-name", f"imajev-{model}", "--host", "127.0.0.1", "--port", str(MODEL_PORT)]
    return argv


def popen_detached(argv: list[str], log: Path, cwd: Path | None = None, env: dict | None = None) -> subprocess.Popen:
    """Start from Python in its own session. (`!nohup ... &` died when the cell ended.)"""
    fh = open(log, "ab")
    return subprocess.Popen(argv, cwd=str(cwd) if cwd else None, env=env, stdout=fh, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, start_new_session=True)


def start_server(model: str) -> subprocess.Popen:
    env = dict(os.environ, PYTHONPATH="src:scripts")
    return popen_detached(server_argv(model), WORK / "imajev-server.log", cwd=imajev_dir(), env=env)


def start_gateway(token: str) -> subprocess.Popen:
    env = dict(os.environ, GATEWAY_TOKEN=token)  # env, not argv: invisible in `ps`
    return popen_detached([sys.executable, str(HERE / "gateway.py"), "--port", str(GATEWAY_PORT),
                           "--backend-port", str(MODEL_PORT)], WORK / "gateway.log", env=env)


def download_cloudflared() -> Path:
    exe = WORK / "cloudflared"
    if not exe.is_file():
        print("downloading cloudflared ...", flush=True)
        urllib.request.urlretrieve(CLOUDFLARED_URL, str(exe))
        exe.chmod(0o755)
    return exe


def parse_tunnel_url(text: str) -> str | None:
    """First https://<name>.trycloudflare.com in cloudflared output. 'api' is Cloudflare's own API host."""
    for m in TUNNEL_RE.finditer(text or ""):
        if m.group(1) != "api":
            return m.group(0)
    return None


def start_tunnel(timeout: float = 90.0) -> tuple[subprocess.Popen, str]:
    exe = download_cloudflared()
    log = WORK / "cloudflared.log"
    log.write_bytes(b"")
    proc = popen_detached([str(exe), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{GATEWAY_PORT}"], log)
    end = time.time() + timeout
    while time.time() < end:
        url = parse_tunnel_url(log.read_text(errors="replace"))
        if url:
            return proc, url
        if proc.poll() is not None:
            raise SystemExit("cloudflared exited early. 로그: " + str(log))
        time.sleep(1)
    raise SystemExit("터널 주소를 찾지 못했습니다. 로그: " + str(log))


def http_get(url: str, token: str | None = None, timeout: float = 8.0) -> tuple[int, bytes]:
    headers = {"User-Agent": "crib-colab-check"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(65536)
    except urllib.error.HTTPError as e:
        return e.code, b""
    except (OSError, http.client.HTTPException):
        return 0, b""


def wait_model_ready(server: subprocess.Popen, timeout: float = 1500.0) -> None:
    """Block until the model server answers GET /v1/models. Stops early if the server process died."""
    end = time.time() + timeout
    last = 0.0
    while time.time() < end:
        if server.poll() is not None:
            print(tail(WORK / "imajev-server.log"), flush=True)
            raise SystemExit("모델 서버가 종료되었습니다. 위 로그를 확인하세요.")
        code, _ = http_get(f"http://127.0.0.1:{MODEL_PORT}/v1/models", timeout=5)
        if code == 200:
            return
        if time.time() - last > 30:
            print("모델 서버 준비 중 ...", flush=True)
            last = time.time()
        time.sleep(3)
    raise SystemExit("모델 서버가 시간 안에 준비되지 않았습니다.")


def tail(path: Path, n: int = 25) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return "(로그 없음)"


def banner(url: str, token: str, model: str) -> str:
    return "\n".join([
        "=" * 64,
        f"  Jev 서버 준비 완료 (imajev-{model}, Colab)",
        "=" * 64,
        "",
        "PC의 crib_remote.env 에 아래 두 줄을 넣거나 환경 변수로 설정하세요",
        "(GUI의 'Jev 서버' 칸에 붙여넣어도 됩니다):",
        "",
        f"  CRIB_JEV_URL={url}",
        f"  CRIB_JEV_TOKEN={token}",
        "",
        "!! 경고",
        "  - 이 주소와 토큰이 있으면 누구든 이 모델(과 이 Colab GPU)을 쓸 수 있습니다. 공유하지 마세요.",
        "  - 이 화면(셀 출력)을 캡처해서 올리지 마세요. 노트북을 공유하기 전에 출력을 지우세요.",
        "  - 아기 사진이 집 밖(공개 터널 → Google Colab)으로 나갑니다.",
        "  - 무료 Colab 런타임은 사용하지 않으면(또는 시간이 지나면) 끊깁니다.",
        "    테스트와 점수 측정용으로 쓰고, 알림의 유일한 경로로 쓰지 마세요. 로컬 서버를 기본으로 두세요.",
        "  - 주소는 런타임/터널을 다시 켤 때마다 바뀌고, 토큰도 새로 만들어집니다.",
        "",
        "속도: 4B는 요청 1건에 약 3초(T4, 워밍업 후), 첫 요청은 더 느립니다. 2B는 VRAM 약 4.6GB, 4B는 약 9.9GB.",
        "=" * 64,
    ])


def launch(model: str = "4b") -> dict:
    """Start server, gateway and tunnel, wait for the model, print the banner. Returns the state for health_loop."""
    model = _check_model(model)
    if not (imajev_dir() / "adapters" / f"imajev-{model}" / "calibration.json").is_file():
        raise SystemExit("먼저 install(MODEL)을 실행하세요.")
    token = secrets.token_urlsafe(32)
    server = start_server(model)
    gateway = start_gateway(token)
    time.sleep(1)
    if gateway.poll() is not None:
        raise SystemExit("gateway 시작 실패: " + tail(WORK / "gateway.log"))
    tunnel, url = start_tunnel()
    wait_model_ready(server)
    # Through the public URL with the token (DNS for a new quick tunnel can take a few seconds).
    code = 0
    for _ in range(20):
        code, _b = http_get(url + "/v1/models", token)
        if code == 200:
            break
        time.sleep(3)
    wrong, _b = http_get(url + "/v1/models", "x" * 20)
    print(banner(url, token, model), flush=True)
    if code != 200:
        print(f"!! 공개 주소로 확인하지 못했습니다 (응답 {code}). 잠시 뒤 PC에서 '연결 테스트'를 눌러 보세요.", flush=True)
    elif wrong != 401:
        print(f"!! 잘못된 토큰이 401이 아닙니다 (응답 {wrong}). 이 주소를 쓰지 마세요.", flush=True)
    return {"model": model, "token": token, "url": url, "server": server, "gateway": gateway, "tunnel": tunnel}


def status_line(state: dict) -> str:
    """One line. Never contains the token or the URL."""
    ok = lambda b: "OK" if b else "끊김"  # noqa: E731
    server_up = state["server"].poll() is None and http_get(f"http://127.0.0.1:{MODEL_PORT}/v1/models", timeout=5)[0] == 200
    gateway_up = state["gateway"].poll() is None
    tunnel_proc = state["tunnel"].poll() is None
    public = http_get(state["url"] + "/v1/models", state["token"], timeout=10)[0] == 200 if tunnel_proc else False
    line = (f"{time.strftime('%H:%M:%S')}  모델 {ok(server_up)}  게이트웨이 {ok(gateway_up)}  "
            f"터널 프로세스 {ok(tunnel_proc)}  공개 주소 {ok(public)}")
    if not public:
        line += "   <<< 터널이 죽었거나 응답하지 않음. 이 셀을 멈추고 launch()를 다시 실행하세요 (주소·토큰이 새로 바뀝니다)"
    return line


def health_loop(state: dict, every: float = 30.0) -> None:
    """Print a status line every `every` seconds until the cell is interrupted."""
    print("상태 확인 시작. 멈추려면 셀을 중단하세요 (서버는 계속 켜져 있습니다).", flush=True)
    try:
        while True:
            print(status_line(state), flush=True)
            time.sleep(every)
    except KeyboardInterrupt:
        print("상태 확인을 멈췄습니다. 서버/터널은 그대로입니다.", flush=True)


def stop(state: dict) -> None:
    for key in ("tunnel", "gateway", "server"):
        proc = state.get(key)
        if proc is not None and proc.poll() is None:
            proc.terminate()
    print("서버, 게이트웨이, 터널을 종료했습니다.", flush=True)


def _selftest() -> None:
    for m in MODELS:
        a = server_argv(m)
        j = " ".join(a)
        assert "--backend torch" in j and f"--model-name imajev-{m}" in j and f"--port {MODEL_PORT}" in j
        assert f"--adapter adapters/imajev-{m}" in j and f"--calibration adapters/imajev-{m}/calibration.json" in j
        assert "--merge-lora" not in a and "--fast" not in a
        assert ("--model-bundle artifacts/model-qwen4b.json" in j) == (m == "4b")
        assert a[1] == "scripts/playground/server.py" and "--host 127.0.0.1" in j
    try:
        server_argv("9b")
        raise SystemExit("bad model accepted")
    except SystemExit as e:
        assert "accepted" not in str(e)
    assert parse_tunnel_url("INF Requesting new quick Tunnel on trycloudflare.com ...\n"
                            "https://api.trycloudflare.com/tunnel failed\n"
                            "|  https://random-words-1234.trycloudflare.com  |") == "https://random-words-1234.trycloudflare.com"
    assert parse_tunnel_url("nothing here") is None and parse_tunnel_url("http://a.trycloudflare.com") is None
    token = secrets.token_urlsafe(32)
    text = banner("https://random-words-1234.trycloudflare.com", token, "4b")
    assert "CRIB_JEV_URL=https://random-words-1234.trycloudflare.com" in text and f"CRIB_JEV_TOKEN={token}" in text
    for word in ("공유하지 마세요", "집 밖", "끊깁니다", "유일한 경로"):
        assert word in text
    # the token must never be in a command line or in a status line
    class P:
        def __init__(self, alive):
            self.alive = alive

        def poll(self):
            return None if self.alive else 1

    st = {"server": P(False), "gateway": P(False), "tunnel": P(False), "url": "https://x-y.trycloudflare.com", "token": token}
    line = status_line(st)
    assert token not in line and "x-y" not in line and "끊김" in line and "<<<" in line
    # gateway env, not argv
    argv_probe = [sys.executable, str(HERE / "gateway.py"), "--port", str(GATEWAY_PORT), "--backend-port", str(MODEL_PORT)]
    assert token not in " ".join(argv_probe + server_argv("4b"))
    assert len(token) >= 40
    print("ok start-colab")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        mdl = sys.argv[1] if len(sys.argv) > 1 else "4b"
        install(mdl)
        st = launch(mdl)
        health_loop(st)
