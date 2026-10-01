"""Crib rules as a local Jev systemone call. Not a medical device.

Wire (loopback only): POST /v1/systemone, Jev body + top-level
images=["data:image/jpeg;base64,..."] (imajev-2b/4b/9b playground server).
The reply must echo usage.images with one entry, else the server ignored the
photo (text-only Jev) and the verdict is dropped.

api.typesafe.ai is refused: baby frames stay on this PC.
Jev does not write reason. Alert text falls back to the rule label.
"""

from __future__ import annotations

import json
import os
import http.client
from urllib.parse import urlparse

from qwen_client import _jpeg_b64, normalize

DEFAULT_URL = "http://127.0.0.1:8090/v1/systemone"
# Unsure band is [ABSENT_AT, ALERT_AT). Not a calibrated Jev threshold.
ALERT_AT = 0.60
ABSENT_AT = 0.30
PRESENT_AT = 0.50
_RULES = ("face_cover", "face_down", "climbing")
# face_down is asked as three visible-cue questions. One question scored ~0.2-0.45 on prone photos.
# The shown face_down score is the SECOND-HIGHEST of these (two cues must agree). Raw values go in face_down_parts.
_FACE_DOWN_PARTS = ("face_down_cheek", "face_down_back", "face_down_belly")
_FIELDS = _RULES + ("baby_present", "adult_present", "face_visible")
_ASKED = tuple(k for k in _FIELDS if k != "face_down") + _FACE_DOWN_PARTS
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})

_QUESTIONS = {
    "baby_present": {
        "type": "noul",
        "instructions": "Is a baby visible in this crib frame?",
        "criteria": {"true": "A baby is in the frame", "false": "No baby"},
    },
    "adult_present": {
        "type": "noul",
        "instructions": "Is an adult visible in this frame? An adult is not a baby.",
        "criteria": {"true": "An adult is visible", "false": "No adult"},
    },
    "face_visible": {
        "type": "noul",
        "instructions": "Is any skin of an eye, cheek, mouth, or nose visible?",
        "criteria": {
            "true": "Eye, cheek, mouth, or nose skin is visible",
            "false": "None of that skin is visible",
        },
    },
    "face_cover": {
        "type": "noul",
        "instructions": (
            "Does fabric cover the mouth AND the nose? "
            "A visible eye or cheek does not cancel this. "
            "Fabric on the body or shoulders only is not a cover."
        ),
        "criteria": {
            "true": "Fabric covers mouth and nose",
            "false": "Mouth and nose are not both covered by fabric",
        },
    },
    "face_down_cheek": {
        "type": "noul",
        "instructions": "Is the baby's cheek or face pressed against the mattress or surface?",
        "criteria": {
            "true": "Cheek or face pressed against the surface",
            "false": "Cheek and face are not pressed against the surface",
        },
    },
    "face_down_back": {
        "type": "noul",
        "instructions": (
            "Do the baby's back, bottom, or the back of the head face up toward "
            "the camera while the baby lies down?"
        ),
        "criteria": {
            "true": "Back, bottom, or back of the head faces up",
            "false": "Back, bottom, and back of the head do not face up",
        },
    },
    "face_down_belly": {
        "type": "noul",
        "instructions": (
            "Is the baby's belly or chest pressed down onto the surface and not visible?"
        ),
        "criteria": {
            "true": "Belly or chest is down on the surface and hidden",
            "false": "Belly or chest is not pressed down and hidden",
        },
    },
    "climbing": {
        "type": "noul",
        "instructions": "Is the baby on the crib rail?",
        "criteria": {"true": "On the crib rail", "false": "Not on the rail"},
    },
}


def alert_at(rule: str) -> float:
    """ALERT_AT, or CRIB_JEV_ALERT_AT_<RULE> (0 < x <= 1). Else stop."""
    name = "CRIB_JEV_ALERT_AT_" + rule.upper()
    raw = os.environ.get(name, "").strip()
    if not raw:
        return ALERT_AT
    try:
        value = float(raw)
    except ValueError:
        raise SystemExit(f"{name} must be a number in (0, 1]: {raw!r}")
    if not (0.0 < value <= 1.0):  # also rejects nan
        raise SystemExit(f"{name} must be in (0, 1]: {raw!r}")
    return value


def questions() -> dict:
    return json.loads(json.dumps(_QUESTIONS))


def build_request(image_jpeg_b64: str, model: str = "jev-latest") -> dict:
    if not image_jpeg_b64:
        raise ValueError("empty frame")
    return {
        "model": model or "jev-latest",
        "images": ["data:image/jpeg;base64," + image_jpeg_b64],
        "state": {"kind": "crib_frame", "image": "attached"},
        "questions": questions(),
    }


def assert_loopback(url: str) -> str:
    """Return the URL if it is http loopback /v1/systemone. Else stop."""
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "http" or host not in _LOOPBACK:
        raise SystemExit("jev url must be http loopback; baby frames stay on this PC")
    if parsed.username or parsed.password:
        raise SystemExit("jev url must not embed credentials")
    if parsed.path.rstrip("/") != "/v1/systemone":
        raise SystemExit("jev url path must be /v1/systemone")
    return (url or "").strip()


def _noul(answers: dict, key: str) -> float:
    raw = answers.get(key) if isinstance(answers, dict) else None
    if not isinstance(raw, dict) or "noul" not in raw:
        raise ValueError(f"jev answer {key} missing noul")
    if raw.get("type") not in (None, "noul"):
        raise ValueError(f"jev answer {key} is not noul")
    value = raw["noul"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"jev answer {key} noul not a number")
    value = float(value)
    if value != value or value < 0.0 or value > 1.0:
        raise ValueError(f"jev answer {key} noul out of range")
    return value


def verdict_from_answers(answers: dict) -> dict:
    """Map Jev nouls onto the crib dict. Code owns should_alert, then normalize."""
    if not isinstance(answers, dict):
        raise ValueError("jev answers missing")
    scores = {key: _noul(answers, key) for key in _ASKED}
    parts = {key: scores.pop(key) for key in _FACE_DOWN_PARTS}
    scores["face_down"] = sorted(parts.values())[-2]  # second highest of 3 cues
    fired = [key for key in _RULES if scores[key] >= alert_at(key)]
    alert = False
    rule = None
    if fired:
        rule = max(fired, key=lambda key: (scores[key], -_RULES.index(key)))
        alert = True
    elif all(scores[key] < ABSENT_AT for key in _RULES) and (
        scores["baby_present"] < ABSENT_AT and scores["adult_present"] < ABSENT_AT
    ):
        rule = "empty"
        alert = True
    got = normalize(
        {
            "should_alert": alert,
            "baby_present": scores["baby_present"] >= PRESENT_AT,
            "face_visible": scores["face_visible"] >= PRESENT_AT,
            "rule": rule,
            "reason": "",
        }
    )
    got["scores"] = scores
    got["face_down_parts"] = parts
    return got


def assert_saw_image(body: dict) -> None:
    usage = body.get("usage") if isinstance(body, dict) else None
    seen = usage.get("images") if isinstance(usage, dict) else None
    if not isinstance(seen, list) or len(seen) != 1:
        raise ValueError("jev server did not read the frame; verdict dropped")


def _read_json(resp, limit: int = 1_000_000) -> dict:
    data = resp.read(limit + 1)
    if len(data) > limit:
        raise ValueError("jev response too large")
    body = json.loads(data.decode("utf-8"))
    if not isinstance(body, dict):
        raise ValueError("jev response not object")
    return body


def _post_json(url: str, payload: dict, timeout: int) -> dict:
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("CRIB_JEV_KEY", "").strip()
    if key:
        headers["Authorization"] = "Bearer " + key
    # http.client never follows redirects, so a frame cannot bounce to a cloud host.
    parsed = urlparse(url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout)
    try:
        conn.request("POST", parsed.path, json.dumps(payload).encode("utf-8"), headers)
        resp = conn.getresponse()
        if resp.status != 200:
            raise RuntimeError(f"jev HTTP {resp.status} refused; frame not resent")
        return _read_json(resp)
    finally:
        conn.close()


def ask(image, timeout: int = 180) -> dict:
    url = assert_loopback(os.environ.get("CRIB_JEV_URL", DEFAULT_URL))
    model = os.environ.get("CRIB_JEV_MODEL", "jev-latest").strip() or "jev-latest"
    out = _post_json(url, build_request(_jpeg_b64(image), model), timeout)
    assert_saw_image(out)
    answers = out.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("jev response missing answers")
    return verdict_from_answers(answers)


def _self_check() -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from PIL import Image

    assert set(questions()) == set(_ASKED) and "face_down" not in questions()
    for item in questions().values():
        assert item["type"] == "noul" and item["instructions"]
        assert set(item["criteria"]) == {"true", "false"}

    cover = {
        "baby_present": {"type": "noul", "noul": 0.96},
        "adult_present": {"noul": 0.02},
        "face_visible": {"noul": 0.10},
        "face_cover": {"noul": 0.91},
        "face_down_cheek": {"noul": 0.04},
        "face_down_back": {"noul": 0.02},
        "face_down_belly": {"noul": 0.03},
        "climbing": {"noul": 0.03},
    }
    hit = verdict_from_answers(cover)
    assert hit["should_alert"] is True and hit["rule"] == "face_cover" and hit["reason"] == ""
    unsure = {key: {"noul": 0.50} for key in _ASKED}
    assert verdict_from_answers(unsure)["should_alert"] is False
    empty = {key: {"noul": 0.05} for key in _ASKED}
    got = verdict_from_answers(empty)
    assert got["should_alert"] is True and got["rule"] == "empty" and got["baby_present"] is False
    clash = {key: {"noul": 0.05} for key in _ASKED}
    clash["face_cover"] = {"noul": 0.95}
    clash["baby_present"] = {"noul": 0.10}
    covered = verdict_from_answers(clash)
    # A baby fully under a blanket may look "absent". Keep the alert.
    assert covered["should_alert"] is True and covered["rule"] == "face_cover"
    assert "아기가 안 보임" in covered["reason"]
    clash["face_cover"] = {"noul": 0.05}
    clash["climbing"] = {"noul": 0.95}
    assert verdict_from_answers(clash)["should_alert"] is False  # climbing needs a visible baby
    # face_down = SECOND-HIGHEST of the three sub-questions; at least two cues must agree.
    low = {key: {"noul": 0.05} for key in _ASKED}
    low.update(baby_present={"noul": 0.95}, face_down_cheek={"noul": 0.20},
               face_down_back={"noul": 0.72}, face_down_belly={"noul": 0.35})
    one = verdict_from_answers(low)
    assert one["scores"]["face_down"] == 0.35 and "face_down_cheek" not in one["scores"]
    assert one["should_alert"] is False and one["rule"] is None
    assert one["face_down_parts"] == {"face_down_cheek": 0.20, "face_down_back": 0.72,
                                      "face_down_belly": 0.35}
    low.update(face_down_cheek={"noul": 0.72}, face_down_back={"noul": 0.70},
               face_down_belly={"noul": 0.10})
    two = verdict_from_answers(low)
    assert two["scores"]["face_down"] == 0.70
    assert two["should_alert"] is True and two["rule"] == "face_down"
    assert two["face_down_parts"]["face_down_cheek"] == 0.72
    assert two["face_down_parts"]["face_down_belly"] == 0.10
    low.update(face_down_cheek={"noul": 0.20}, face_down_back={"noul": 0.72},
               face_down_belly={"noul": 0.35})
    del low["face_down_belly"]  # a missing sub-answer is an error, not 0
    try:
        verdict_from_answers(low)
        raise SystemExit("missing sub-answer accepted")
    except ValueError:
        pass
    # per-rule env threshold
    prev_env = os.environ.get("CRIB_JEV_ALERT_AT_FACE_DOWN")
    try:
        os.environ.pop("CRIB_JEV_ALERT_AT_FACE_DOWN", None)
        assert alert_at("face_down") == ALERT_AT
        low["face_down_belly"] = {"noul": 0.45}  # second highest = 0.45
        os.environ["CRIB_JEV_ALERT_AT_FACE_DOWN"] = "0.40"
        assert alert_at("face_down") == 0.40 and alert_at("face_cover") == ALERT_AT
        assert verdict_from_answers(low)["rule"] == "face_down"  # 0.45 >= 0.40
        for bad in ("0", "-0.1", "1.5", "nan", "abc"):
            os.environ["CRIB_JEV_ALERT_AT_FACE_DOWN"] = bad
            try:
                alert_at("face_down")
                raise SystemExit(f"accepted threshold {bad}")
            except SystemExit as e:
                if "accepted" in str(e):
                    raise
    finally:
        if prev_env is None:
            os.environ.pop("CRIB_JEV_ALERT_AT_FACE_DOWN", None)
        else:
            os.environ["CRIB_JEV_ALERT_AT_FACE_DOWN"] = prev_env
    try:
        _noul({"face_cover": {"noul": True}}, "face_cover")
        raise SystemExit("bool noul must fail")
    except ValueError:
        pass
    for bad in (
        "https://api.typesafe.ai/v1/systemone",
        "http://api.typesafe.ai/v1/systemone",
        "http://8.8.8.8/v1/systemone",
        "http://127.0.0.1:8090/v1/chat/completions",
        "http://user:pass@127.0.0.1:8090/v1/systemone",
    ):
        try:
            assert_loopback(bad)
            raise SystemExit(f"accepted {bad}")
        except SystemExit as e:
            if "accepted" in str(e):
                raise
    assert assert_loopback("http://127.0.0.1:8090/v1/systemone/")

    posted = {"n": 0}

    def reply(handler, obj):
        body = json.dumps(obj).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    class Quiet(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def finish(self):
            # Drain before the file objects close. shutdown_request's SHUT_WR is too late on Windows
            # and still reset about 1/40 replies.
            import socket

            try:
                if not self.wfile.closed:
                    self.wfile.flush()
                self.connection.shutdown(socket.SHUT_WR)
                self.connection.settimeout(2)
                while self.connection.recv(4096):
                    pass
            except OSError:
                pass
            super().finish()

    class Ok(Quiet):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(n))
            assert req["images"][0].startswith("data:image/jpeg;base64,")
            assert "image_jpeg_b64" not in req["state"]
            assert req["questions"]["face_cover"]["type"] == "noul"
            posted["n"] += 1
            body = json.dumps(
                {"model": "imajev-2b", "answers": cover, "usage": {"images": [{"width": 8}]}}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class Blind(Quiet):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            reply(self, {"answers": cover, "usage": {"images": []}})

    class Bounce(Quiet):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(307)
            self.send_header("Location", "https://api.typesafe.ai/v1/systemone")
            self.send_header("Content-Length", "0")
            self.end_headers()

    class CloseOnly(ThreadingHTTPServer):
        # Quiet.finish already half-closed and drained. SHUT_WR in shutdown_request still reset replies.
        def shutdown_request(self, request):
            self.close_request(request)

    def serve(handler):
        httpd = CloseOnly(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd

    img = Image.new("RGB", (8, 8), (240, 240, 240))
    ok = serve(Ok)
    blind = serve(Blind)
    bounce = serve(Bounce)
    prev = os.environ.get("CRIB_JEV_URL")
    try:
        os.environ["CRIB_JEV_URL"] = f"http://127.0.0.1:{ok.server_address[1]}/v1/systemone"
        live = ask(img, timeout=10)
        assert live["rule"] == "face_cover" and posted["n"] == 1
        os.environ["CRIB_JEV_URL"] = f"http://127.0.0.1:{blind.server_address[1]}/v1/systemone"
        try:
            ask(img, timeout=5)
            raise SystemExit("blind verdict kept")
        except ValueError as e:
            assert "did not read the frame" in str(e)
        os.environ["CRIB_JEV_URL"] = f"http://127.0.0.1:{bounce.server_address[1]}/v1/systemone"
        try:
            ask(img, timeout=5)
            raise SystemExit("redirect followed")
        except RuntimeError as e:
            # HTTP errors must be a plain Exception so the watch loop survives them.
            assert "refused" in str(e) and not isinstance(e, SystemExit)
        assert posted["n"] == 1
    finally:
        if prev is None:
            os.environ.pop("CRIB_JEV_URL", None)
        else:
            os.environ["CRIB_JEV_URL"] = prev
        for httpd in (ok, blind, bounce):
            httpd.shutdown()
            httpd.server_close()
    print("ok jev-protocol")


if __name__ == "__main__":
    _self_check()
