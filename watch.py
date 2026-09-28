"""Crib watch loop. stream1 TCP only. Not a medical device. Not a nanny."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

from PIL import Image

from motion import changed_fraction, should_wake, to_gray
from ntfy_alert import notify, notify_alert
from judge import ask

DIR = Path(__file__).resolve().parent
RTSP_ENV = DIR / "rtsp.env"
FRAME = DIR / "frames" / "latest.jpg"
STATUS = DIR / "frames" / "status.json"
STATUS_HTML = DIR / "frames" / "status.html"
LOG = DIR / "frames" / "ticks.jsonl"
_TICK_N = 0
# ponytail: 15s until GPU heat says otherwise.
INTERVAL_SEC = 15
# ponytail: re-ask even if still; 2 min until GPU heat says otherwise.
FORCE_SEC = 120
COOLDOWN_SEC = 600
GRAB_TIMEOUT_SEC = 25
ERROR_ALERT_AFTER = 3
WATCH_HINT = {
    "grab failed": "캠 화면을 못 받음. 전원, 와이파이, Tapo 앱을 확인하세요",
}


def watch_hint(reason: str) -> str:
    return WATCH_HINT.get(reason, "감시가 끊김. Tapo 앱과 PC를 확인하세요")


def _parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def load_rtsp_url() -> str:
    env = _parse_env(RTSP_ENV)
    user = os.environ.get("RTSP_USER", env.get("RTSP_USER", "")).strip()
    password = os.environ.get("RTSP_PASS", env.get("RTSP_PASS", "")).strip()
    host = os.environ.get("RTSP_HOST", env.get("RTSP_HOST", "")).strip()
    path = os.environ.get("RTSP_PATH", env.get("RTSP_PATH", "stream1")).strip()
    if not user or not password or not host:
        raise SystemExit("rtsp.env needs RTSP_USER RTSP_PASS RTSP_HOST")
    if "/" in host or host.lower().startswith("rtsp"):
        raise SystemExit("RTSP_HOST must be host or host:port only")
    if ":" not in host:
        host = host + ":554"
    if not path.startswith("/"):
        path = "/" + path
    if "stream6" in path.lower():
        raise SystemExit("stream6 is human PTZ, not the alert loop")
    return f"rtsp://{quote(user, safe='')}:{quote(password, safe='')}@{host}{path}"


class GrabError(Exception):
    pass


def grab_rtsp(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-rtsp_transport",
        "tcp",
        "-timeout",
        "8000000",
        "-i",
        url,
        "-frames:v",
        "1",
        "-an",
        "-q:v",
        "3",
        str(dest),
    ]
    try:
        subprocess.check_call(cmd, timeout=GRAB_TIMEOUT_SEC)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise GrabError("ffmpeg grab failed") from e
    if not dest.is_file() or dest.stat().st_size < 1000:
        raise GrabError("ffmpeg grab empty")


class Cooldown:
    def __init__(self, seconds: int = COOLDOWN_SEC) -> None:
        self.seconds = seconds
        self.last_at: dict[str, float] = {}

    def allow(self, rule: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        if rule not in self.last_at:
            return True
        return (now - self.last_at[rule]) >= self.seconds

    def mark(self, rule: str, now: float | None = None) -> None:
        self.last_at[rule] = time.time() if now is None else now


def decide(image: Image.Image, prev_gray=None, force: bool = False) -> tuple[dict, object]:
    gray = to_gray(image)
    motion = True
    if prev_gray is not None:
        motion = should_wake(changed_fraction(prev_gray, gray))
    if prev_gray is not None and not motion and not force:
        return {
            "should_alert": False,
            "baby_present": None,
            "face_visible": None,
            "rule": None,
            "reason": "",
            "motion": False,
            "skipped": True,
        }, gray
    result = ask(image)
    result["motion"] = motion
    result["skipped"] = False
    return result, gray


def maybe_alert(result: dict, cool: Cooldown, send: bool) -> str:
    if result.get("skipped"):
        return "skip"
    if result.get("error"):
        return "error"
    if not result.get("should_alert"):
        return "quiet"
    rule = result.get("rule") or "unknown"
    if not cool.allow(rule):
        return "cooldown"
    if send:
        try:
            notify_alert(rule if rule != "unknown" else None, result.get("reason") or "")
        except Exception:
            return "send_fail"
        cool.mark(rule)
        return "sent"
    cool.mark(rule)
    return "dry"


def _print_tick(result: dict, action: str) -> None:
    global _TICK_N
    _TICK_N += 1
    keys = ("should_alert", "baby_present", "face_visible", "rule", "reason", "motion", "skipped")
    row = {
        "n": _TICK_N,
        "ts": time.strftime("%H:%M:%S"),
        "action": action,
        **{k: result.get(k) for k in keys},
    }
    line = json.dumps(row, ensure_ascii=False)
    print(line, flush=True)
    FRAME.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(line + "\n", encoding="utf-8")
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    STATUS_HTML.write_text(
        "<!doctype html><meta charset=utf-8><title>crib watch</title>"
        "<pre style='font:16px/1.4 ui-monospace,monospace;padding:12px'>"
        f"{line}</pre>",
        encoding="utf-8",
    )


def run_one(
    image: Image.Image,
    cool: Cooldown,
    send: bool,
    prev_gray=None,
    force: bool = False,
):
    try:
        result, gray = decide(image, prev_gray, force=force)
    except Exception as e:
        result = {
            "should_alert": False,
            "baby_present": None,
            "face_visible": None,
            "rule": None,
            "reason": type(e).__name__,
            "motion": None,
            "skipped": False,
            "error": True,
        }
        gray = prev_gray
    action = maybe_alert(result, cool, send)
    _print_tick(result, action)
    return gray, not result.get("skipped") and not result.get("error")


def _error_tick(reason: str, cool: Cooldown, send: bool, fails: int) -> None:
    result = {
        "should_alert": False,
        "baby_present": None,
        "face_visible": None,
        "rule": None,
        "reason": reason,
        "motion": None,
        "skipped": False,
        "error": True,
    }
    _print_tick(result, "error")
    if send and fails == ERROR_ALERT_AFTER:
        try:
            notify(watch_hint(reason), title="감시")
        except Exception:
            pass


def run_rtsp(once: bool, send: bool, ticks: int = 0) -> None:
    url = load_rtsp_url()
    cool = Cooldown()
    prev = None
    n = 0
    fails = 0
    last_infer = 0.0
    while True:
        try:
            grab_rtsp(url, FRAME)
            fails = 0
            with Image.open(FRAME) as img:
                img = img.convert("RGB")
                force = (time.time() - last_infer) >= FORCE_SEC
                prev, did_infer = run_one(img, cool, send, prev, force=force)
                if did_infer:
                    last_infer = time.time()
        except GrabError:
            fails += 1
            _error_tick("grab failed", cool, send, fails)
        n += 1
        if once or (ticks and n >= ticks):
            return
        time.sleep(1 if ticks else INTERVAL_SEC)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--image", help="JPEG/PNG for one-shot (no camera)")
    p.add_argument("--rtsp", action="store_true", help="grab stream1 from rtsp.env")
    p.add_argument("--once", action="store_true")
    p.add_argument("--ticks", type=int, default=0, help="stop after N frames")
    p.add_argument("--send", action="store_true", help="actually ntfy")
    args = p.parse_args()
    cool = Cooldown()
    if args.image:
        img = Image.open(args.image).convert("RGB")
        run_one(img, cool, args.send)
        return
    if args.rtsp or RTSP_ENV.is_file():
        run_rtsp(once=args.once, send=args.send, ticks=args.ticks)
        return
    raise SystemExit("need --image or rtsp.env")


if __name__ == "__main__":
    import sys

    c = Cooldown(seconds=10)
    assert c.allow("face_cover", now=1)
    c.mark("face_cover", now=1)
    assert not c.allow("face_cover", now=5)
    assert c.allow("empty", now=6)
    c.mark("empty", now=6)
    assert not c.allow("empty", now=8)
    assert c.allow("face_cover", now=20)
    print("ok cooldown")
    if len(sys.argv) > 1:
        main()
