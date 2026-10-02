"""Self-test for the live-watch path with the remote Jev. python test_remote_live.py

Local stub servers only (needs the `openssl` CLI for a throwaway certificate; without it the TLS
cases are skipped and say so). Covers: live watch stays local unless CRIB_JEV_REMOTE_LIVE=1 (remote stub
is never contacted), backend='remote' is the only marker in ticks/score log, and a dropped connection /
401 / bad setting on the remote becomes error ticks plus a '판정 불가' push (never a safe score, never a
silent fallback to local, no token or host in any output). notify/notify_alert are replaced: no real ntfy.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import ssl
import tempfile
from pathlib import Path

from PIL import Image

import jev_protocol as J
from test_remote import CLEAN, COVER, QUIET, TOKEN, Stub, env, envc, envd, leak_free, make_cert  # noqa: F401


def live_rules(tmp: Path) -> None:
    img = Image.new("RGB", (8, 8), (240, 240, 240))
    with envc(CRIB_REMOTE_ENV=str(tmp / "none.env")):
        assert not J.remote_live_active()
        with env(CRIB_JEV_URL="https://abc-def.invalid"):
            with env(CRIB_JEV_REMOTE_LIVE="1"):  # live + opt-in + no token: error, not a crash
                try:
                    J.ask(img, live=True)
                    raise SystemExit("https without token accepted (live)")
                except J.RemoteJevError as e:
                    leak_free(e, "abc-def")
        with env(CRIB_JEV_URL="https://abc-def.trycloudflare.com", CRIB_JEV_TOKEN=TOKEN):
            with contextlib.redirect_stderr(io.StringIO()):
                assert J.resolve_endpoint(live=True).kind == "local"
            assert not J.remote_live_active()
            with env(CRIB_JEV_REMOTE_LIVE="1"):
                assert J.resolve_endpoint(live=True).kind == "remote" and J.remote_live_active()
                assert J.resolve_endpoint(live=False).kind == "remote"
            with env(CRIB_JEV_REMOTE_LIVE="0"):
                assert not J.remote_live_active()
    print("  ok live opt-in rules")


def logs_and_live(tmp: Path, certs) -> None:
    import score_log
    import watch

    cert, key = certs
    srv_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    srv_ctx.load_cert_chain(str(cert), str(key))
    stub = Stub(srv_ctx)
    url = f"https://localhost:{stub.port}"
    log = tmp / "scores.jsonl"
    saved_paths = (watch.FRAME, watch.STATUS, watch.STATUS_HTML, watch.LOG, watch.notify, watch.notify_alert)
    watch.FRAME, watch.STATUS = tmp / "f" / "latest.jpg", tmp / "f" / "status.json"
    watch.STATUS_HTML, watch.LOG = tmp / "f" / "status.html", tmp / "f" / "ticks.jsonl"
    pushed: list[tuple] = []
    alerts: list[tuple] = []
    watch.notify = lambda m, title="감시", priority=5: pushed.append((m, priority))  # never the real ntfy
    watch.notify_alert = lambda rule, reason="": alerts.append((rule, reason))
    img = Image.new("RGB", (64, 48), (200, 200, 200))
    try:
        base = dict(CLEAN, CRIB_REMOTE_ENV=str(tmp / "none.env"), SSL_CERT_FILE=str(cert),
                    CRIB_SCORE_LOG=str(log), CRIB_LOG_SAVE_FRAMES=None, CRIB_LOG_SAVE_AMBIGUOUS=None)
        # 1) remote configured, live NOT opted in: live watch never touches the remote stub
        with envd(base, CRIB_JEV_URL=url, CRIB_JEV_TOKEN=TOKEN), contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            mt = watch.ErrorTracker("모델", watch.MODEL_HINT, after=1)
            watch.run_one(img, watch.Cooldown(), True, None, model_errors=mt)  # local 8090 is down: error tick
        assert not stub.seen, "live watch used remote without CRIB_JEV_REMOTE_LIVE"
        assert pushed and pushed[-1][0] == watch.MODEL_HINT  # unchanged local wording
        tick = json.loads(watch.LOG.read_text(encoding="utf-8").splitlines()[-1])
        assert "backend" not in tick and tick["action"] == "error"
        # 2) opted in, remote answers: verdict, tick and score log carry only backend=remote
        pushed.clear()
        out = io.StringIO()
        with envd(base, CRIB_JEV_URL=url, CRIB_JEV_TOKEN=TOKEN, CRIB_JEV_REMOTE_LIVE="1"), \
                contextlib.redirect_stdout(out):
            mt = watch.ErrorTracker("모델", watch.MODEL_HINT, after=3)
            watch.run_one(img, watch.Cooldown(), True, None, model_errors=mt)
        assert stub.seen and stub.seen[-1]["auth"] == "Bearer " + TOKEN
        tick = json.loads(out.getvalue().splitlines()[-1])
        assert tick["backend"] == "remote" and tick["rule"] == "face_cover" and tick["action"] == "sent"
        assert alerts == [("face_cover", "")]
        row = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
        assert row["backend"] == "remote" and row["model"] == "jev"
        pushed.clear()
        # 3) opted in, connection dropped: error ticks, NO safe score, '판정 불가' push after N failures
        stub.mode = "drop"
        out = io.StringIO()
        errs = io.StringIO()
        with envd(base, CRIB_JEV_URL=url, CRIB_JEV_TOKEN=TOKEN, CRIB_JEV_REMOTE_LIVE="1"), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(errs):
            mt = watch.ErrorTracker("모델", watch.MODEL_HINT, after=3)
            for i in range(3):
                gray, did = watch.run_one(img, watch.Cooldown(), True, None, model_errors=mt)
                assert did is False
                assert not pushed or i == 2  # not before the 3rd failure (existing ErrorTracker rule)
        ticks = [json.loads(x) for x in out.getvalue().splitlines()]
        assert len(ticks) == 3
        for t in ticks:
            assert t["action"] == "error" and t["should_alert"] is False and t["rule"] is None
            assert t["baby_present"] is None and t["backend"] == "remote" and t["reason"] == "RemoteJevError"
        assert len(pushed) == 1 and pushed[0][1] == 5 and "판정 불가" in pushed[0][0]
        assert pushed[0][0] == watch.REMOTE_MODEL_HINT
        blob = out.getvalue() + errs.getvalue() + watch.LOG.read_text(encoding="utf-8") \
            + watch.STATUS.read_text(encoding="utf-8") + watch.STATUS_HTML.read_text(encoding="utf-8") + pushed[0][0]
        assert TOKEN not in blob and "localhost" not in blob and str(stub.port) not in blob
        assert not any(json.loads(l).get("scores") for l in log.read_text(encoding="utf-8").splitlines()[2:])
        # 401 behaves the same
        stub.mode = "ok"
        pushed.clear()
        with envd(base, CRIB_JEV_URL=url, CRIB_JEV_TOKEN="rejected_token_value", CRIB_JEV_REMOTE_LIVE="1"), \
                contextlib.redirect_stdout(io.StringIO()):
            mt = watch.ErrorTracker("모델", watch.MODEL_HINT, after=1)
            watch.run_one(img, watch.Cooldown(), True, None, model_errors=mt)
        assert pushed and "판정 불가" in pushed[0][0]
        # recovery push once the remote answers again
        pushed.clear()
        with envd(base, CRIB_JEV_URL=url, CRIB_JEV_TOKEN=TOKEN, CRIB_JEV_REMOTE_LIVE="1"), \
                contextlib.redirect_stdout(io.StringIO()):
            watch.run_one(img, watch.Cooldown(), True, None, model_errors=mt)
        assert any(p[0].startswith("감시 복구됨") for p in pushed)
        # 4) a bad remote setting while live is an error tick + push, not a crash or a safe score
        pushed.clear()
        with envd(base, CRIB_JEV_URL=url, CRIB_JEV_TOKEN="has space", CRIB_JEV_REMOTE_LIVE="1"), \
                contextlib.redirect_stdout(io.StringIO()):
            mt = watch.ErrorTracker("모델", watch.MODEL_HINT, after=1)
            watch.run_one(img, watch.Cooldown(), True, None, model_errors=mt)
        assert pushed and "판정 불가" in pushed[0][0]
        # 5) local results never get a backend marker; score log row for local has no 'backend'
        with envd(base):
            score_log.log_judgment({"should_alert": False, "scores": {}}, "jev", "watch")
        assert "backend" not in json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    finally:
        watch.FRAME, watch.STATUS, watch.STATUS_HTML, watch.LOG, watch.notify, watch.notify_alert = saved_paths
        stub.close()
    print("  ok live path: stays local by default; remote drop/401/bad config -> error ticks + '판정 불가' push")


def main() -> None:
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        live_rules(tmp)
        certs = make_cert(tmp)
        if certs is None:
            print("  SKIPPED TLS stub cases: need the `openssl` command to make a test certificate")
        else:
            logs_and_live(tmp, certs)
    print("ok remote-live")


if __name__ == "__main__":
    main()
