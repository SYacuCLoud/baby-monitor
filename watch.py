"""Crib watch loop. stream1 TCP only. Not a medical device. Not a nanny."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

from PIL import Image

from motion import changed_fraction, should_wake, to_gray
from ntfy_alert import notify, notify_alert
from judge import ask, model_kind
from score_log import log_judgment

DIR = Path(__file__).resolve().parent
RTSP_ENV = DIR / "rtsp.env"
FRAME = DIR / "frames" / "latest.jpg"
STATUS = DIR / "frames" / "status.json"
STATUS_HTML = DIR / "frames" / "status.html"
LOG = DIR / "frames" / "ticks.jsonl"
_TICK_N = 0
# ponytail: 15s until GPU heat says otherwise.
INTERVAL_SEC = 15
# Re-ask even if the picture is still. Was 120s; a blanket creeping up is slow and gated out.
FORCE_SEC = 60
COOLDOWN_SEC = 600
GRAB_TIMEOUT_SEC = 25
ERROR_ALERT_AFTER = 3
# Keep telling the phone while the watcher stays broken (once is easy to miss).
ERROR_REALERT_SEC = 1800
# "Still watching" push so a dead PC / sleep / network loss is noticed. 0 disables.
HEARTBEAT_SEC = int(os.environ.get("WATCH_HEARTBEAT_SEC", "21600"))
WATCH_HINT = {
    "grab failed": "캠 화면을 못 받음. 전원, 와이파이, Tapo 앱을 확인하세요",
}
MODEL_HINT = "판정 모델 오류. llama-server나 Jev 서버, PC를 확인하세요"


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


class ErrorTracker:
    """Phone push when a failure repeats: at N in a row, again every REALERT_SEC, and once on recovery."""

    def __init__(self, label: str, hint: str, after: int = ERROR_ALERT_AFTER,
                 realert_sec: int = ERROR_REALERT_SEC) -> None:
        self.label, self.hint, self.after, self.realert_sec = label, hint, after, realert_sec
        self.fails = 0
        self.last_push = 0.0
        self.pushed = False

    def _push(self, text: str, send: bool, title: str = "감시") -> bool:
        if not send:
            return False
        try:
            notify(text, title=title)
            return True
        except Exception as e:
            print(f"[{self.label}] push failed: {type(e).__name__}", file=sys.stderr, flush=True)
            return False

    def fail(self, send: bool, now: float | None = None, hint: str | None = None) -> None:
        now = time.time() if now is None else now
        self.fails += 1
        due = self.fails >= self.after and (
            not self.pushed or (now - self.last_push) >= self.realert_sec
        )
        if due and self._push(hint or self.hint, send):
            self.pushed = True
            self.last_push = now

    def ok(self, send: bool) -> None:
        if self.pushed:
            self._push("감시 복구됨 (" + self.label + ")", send)
        self.fails = 0
        self.pushed = False


class Heartbeat:
    def __init__(self, seconds: int = HEARTBEAT_SEC) -> None:
        self.seconds = seconds
        self.last = time.time()

    def due(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return self.seconds > 0 and (now - self.last) >= self.seconds

    def beat(self, send: bool, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if not self.due(now):
            return
        self.last = now
        if send:
            try:
                notify("감시 중. 이 알림이 끊기면 PC와 캠을 확인하세요", title="감시", priority=3)
            except Exception as e:
                print(f"[heartbeat] push failed: {type(e).__name__}", file=sys.stderr, flush=True)


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


def decide(
    image: Image.Image, prev_gray=None, force: bool = False, base_gray=None
) -> tuple[dict, object]:
    gray = to_gray(image)
    motion = True
    if prev_gray is not None:
        # Compare with the last frame AND the last judged frame. Frame-to-frame alone never
        # sees a slow change (blanket creeping up) because each step is under the threshold.
        motion = should_wake(changed_fraction(prev_gray, gray))
        if not motion and base_gray is not None:
            motion = should_wake(changed_fraction(base_gray, gray))
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
    log_judgment(result, model_kind(), "watch", image)  # never raises
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
    if result.get("camera"):
        row["camera"] = result["camera"]
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
    base_gray=None,
    model_errors: "ErrorTracker | None" = None,
    camera: str | None = None,
):
    try:
        result, gray = decide(image, prev_gray, force=force, base_gray=base_gray)
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
    if camera:
        result["camera"] = camera
    action = maybe_alert(result, cool, send)
    _print_tick(result, action)
    if model_errors is not None:
        if result.get("error"):
            model_errors.fail(send)
        elif not result.get("skipped"):
            model_errors.ok(send)
    return gray, not result.get("skipped") and not result.get("error")


def _error_tick(reason: str, camera: str | None = None) -> None:
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
    if camera:
        result["camera"] = camera
    _print_tick(result, "error")


def run_rtsp(once: bool, send: bool, ticks: int = 0) -> None:
    url = load_rtsp_url()
    cool = Cooldown()
    grab_errors = ErrorTracker("캠", "")
    model_errors = ErrorTracker("모델", MODEL_HINT)
    beat = Heartbeat()
    prev = None
    base = None
    n = 0
    last_infer = 0.0
    while True:
        try:
            grab_rtsp(url, FRAME)
            with Image.open(FRAME) as img:
                img = img.convert("RGB")
            grab_errors.ok(send)
            force = (time.time() - last_infer) >= FORCE_SEC
            prev, did_infer = run_one(
                img, cool, send, prev, force=force, base_gray=base, model_errors=model_errors,
                camera="ok",
            )
            if did_infer:
                last_infer = time.time()
                base = prev
        except Exception as e:
            # GrabError, truncated JPEG (OSError), anything else: report it, keep watching.
            reason = "grab failed" if isinstance(e, GrabError) else type(e).__name__
            _error_tick(reason, camera="down")
            grab_errors.fail(send, hint=watch_hint(reason))
        # Only say "still watching" when it is true: camera and model both OK and a recent verdict.
        healthy = (
            grab_errors.fails == 0
            and model_errors.fails == 0
            and (time.time() - last_infer) < FORCE_SEC * 3
        )
        if healthy:
            beat.beat(send)
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


if __name__ == "__main__" and (len(sys.argv) == 1 or "--selftest" in sys.argv[1:]):
    # Self-test only: it swaps global ask/notify for stubs, so it must never run
    # in the same process as the real CLI (main) or the real push/model get lost.
    _prev_log = os.environ.get("CRIB_SCORE_LOG")
    os.environ["CRIB_SCORE_LOG"] = "off"  # self-test must not write a real log
    c = Cooldown(seconds=10)
    assert c.allow("face_cover", now=1)
    c.mark("face_cover", now=1)
    assert not c.allow("face_cover", now=5)
    assert c.allow("empty", now=6)
    c.mark("empty", now=6)
    assert not c.allow("empty", now=8)
    assert c.allow("face_cover", now=20)
    print("ok cooldown")

    # --- failure reporting ---
    sent = []
    notify = lambda m, title="감시", priority=5: sent.append((m, priority))
    t = ErrorTracker("모델", "model down", after=3, realert_sec=100)
    t.fail(True, now=0); t.fail(True, now=1)
    assert not sent
    t.fail(True, now=2)
    assert sent == [("model down", 5)]
    t.fail(True, now=50)
    assert len(sent) == 1  # no spam inside the re-alert window
    t.fail(True, now=150)
    assert len(sent) == 2  # re-alert while still broken
    t.ok(True)
    assert sent[-1][0].startswith("감시 복구됨") and t.fails == 0
    t.ok(True)
    n_before = len(sent)
    t.ok(True)
    assert len(sent) == n_before
    hb = Heartbeat(seconds=100)
    hb.last = 0
    assert not hb.due(now=50) and hb.due(now=100)
    hb.beat(True, now=100)
    assert sent[-1][1] == 3 and not hb.due(now=150)
    assert not Heartbeat(seconds=0).due(now=10**9)

    # --- slow change is caught by the baseline compare ---
    from PIL import Image as _I
    base_img = _I.new("RGB", (64, 48), (20, 20, 20))
    g0 = to_gray(base_img)
    step1 = base_img.copy(); step1.paste((200, 200, 200), (0, 0, 4, 8))     # ~1% of frame
    step2 = step1.copy();    step2.paste((200, 200, 200), (4, 0, 8, 8))
    step3 = step2.copy();    step3.paste((200, 200, 200), (8, 0, 12, 8))   # ~3% total vs base
    calls = []
    ask = lambda im: calls.append(1) or {"should_alert": False}
    r, g1 = decide(step1, g0)
    assert r["skipped"]
    r, g2 = decide(step2, g1)
    assert r["skipped"]
    r, g3 = decide(step3, g2)
    assert r["skipped"], "frame-to-frame only misses the drift"
    r, _ = decide(step3, g2, base_gray=g0)
    assert not r["skipped"] and calls, "baseline compare must catch the slow drift"

    # --- loop survives a corrupt frame and a model exception ---
    class _Boom(Exception):
        pass
    def _bad_ask(im):
        raise _Boom()
    ask = _bad_ask
    mt = ErrorTracker("모델", "model down", after=1)
    sent.clear()
    run_one(base_img, Cooldown(), True, None, model_errors=mt)
    assert sent and sent[0][0] == "model down"
    print("ok failures")
    if _prev_log is None:
        os.environ.pop("CRIB_SCORE_LOG", None)
    else:
        os.environ["CRIB_SCORE_LOG"] = _prev_log
elif __name__ == "__main__":
    main()
