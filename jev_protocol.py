"""Crib rules as a local Jev systemone call. Not a medical device.

Wire: POST /v1/systemone, Jev body + top-level
images=["data:image/jpeg;base64,..."] (imajev-2b/4b/9b playground server).
Default: http loopback only. Opt-in remote mode (any Jev-compatible web API, e.g. Jev on Google Colab behind
an https tunnel): CRIB_JEV_URL + CRIB_JEV_TOKEN (see remote_settings.py). The address may carry its own path.
localhost and private-network addresses may use plain http and no token; every other address needs https AND
a token. The token goes only in the Authorization header. Live watch uses the remote server only with
CRIB_JEV_REMOTE_LIVE=1. Redirects are never followed.
The reply must echo usage.images with one entry, else the server ignored the
photo (text-only Jev) and the verdict is dropped.

api.typesafe.ai is refused: baby frames stay on this PC.
Jev does not write reason. Alert text falls back to the rule label.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
import http.client
import socket
import ssl
import sys
from dataclasses import dataclass
from urllib.parse import urlparse

import remote_settings
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


class RemoteJevError(RuntimeError):
    """Remote Jev failed (network, TLS, HTTP, config). A plain Exception so the watch loop
    reports it through its normal model-error path. The text never holds the URL or token."""


@dataclass(frozen=True)
class Endpoint:
    kind: str  # 'local' | 'remote'
    host: str
    port: int
    path: str  # request path of the systemone call
    token: str = ""
    scheme: str = "https"  # remote only: 'http' is allowed just for localhost / private network

    def __repr__(self) -> str:  # no token, masked host
        where = remote_settings.mask_host(self.host) if self.kind == "remote" else f"{self.host}:{self.port}"
        return f"Endpoint({self.kind}, {where})"


_LIVE: contextvars.ContextVar[bool] = contextvars.ContextVar("crib_jev_live", default=False)
_warned_live = False


@contextlib.contextmanager
def live_context():
    """Mark calls inside as live monitoring (watch.py). Remote is used there only with CRIB_JEV_REMOTE_LIVE=1."""
    tok = _LIVE.set(True)
    try:
        yield
    finally:
        _LIVE.reset(tok)


def _local_endpoint(url: str) -> Endpoint:
    url = assert_loopback(url)
    parsed = urlparse(url)
    return Endpoint("local", parsed.hostname, parsed.port or 80, parsed.path)


def _remote_endpoint(s: "remote_settings.Settings") -> Endpoint:
    try:
        u = remote_settings.parse_url(s.url)
        token = remote_settings.validate_token(s.token, u.token_required)
    except remote_settings.SettingsError as e:
        raise RemoteJevError("remote jev settings invalid: " + str(e)) from None
    return Endpoint("remote", u.host, u.port, u.path, token, u.scheme)


def endpoint_from_settings(s: "remote_settings.Settings") -> Endpoint:
    """Endpoint for a Settings object (GUI connection test). Remote URL -> remote rules, else the local rules."""
    if s.remote_url_configured:
        return _remote_endpoint(s)
    env_url = os.environ.get("CRIB_JEV_URL", "").strip()
    local = urlparse(env_url)
    return _local_endpoint(env_url if local.scheme == "http" and local.hostname in remote_settings.LOOPBACK else DEFAULT_URL)


def resolve_endpoint(live: bool = False, environ=None) -> Endpoint:
    """Pick where this call goes. Default (nothing configured) is exactly the old local behavior."""
    global _warned_live
    environ = os.environ if environ is None else environ
    s = remote_settings.load(environ)
    if not s.remote_url_configured:
        # Legacy path: CRIB_JEV_URL (default DEFAULT_URL) must be http loopback /v1/systemone, else SystemExit.
        return _local_endpoint(environ.get("CRIB_JEV_URL", DEFAULT_URL))
    if live and not s.remote_live:
        if not _warned_live:
            _warned_live = True
            print("[jev] remote configured but live watch stays local (CRIB_JEV_REMOTE_LIVE is off)",
                  file=sys.stderr, flush=True)
        return _local_endpoint(DEFAULT_URL)
    return _remote_endpoint(s)


def remote_live_active(environ=None) -> bool:
    """True if live watch would use the remote backend. Never raises."""
    try:
        s = remote_settings.load(environ)
        return remote_settings.uses_remote(s, True)
    except Exception:
        return False


def _timeout_for(ep: Endpoint, timeout, environ=None):
    if ep.kind != "remote":
        return timeout
    environ = os.environ if environ is None else environ
    try:
        custom = remote_settings.parse_timeout(remote_settings.load(environ).timeout)
    except remote_settings.SettingsError as e:
        raise RemoteJevError("remote jev settings invalid: " + str(e)) from None
    return custom if custom is not None else timeout


def _remote_error(exc: BaseException) -> RemoteJevError:
    if isinstance(exc, (socket.timeout, TimeoutError)):
        what = "timeout"
    elif isinstance(exc, ssl.SSLError):
        what = "tls error"
    else:
        what = "connection failed"
    return RemoteJevError(f"remote jev {what}")


def _exchange(ep: Endpoint, method: str, path: str, payload: dict | None, timeout) -> dict:
    headers = {}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    if ep.kind == "remote":
        if ep.token:  # header only, never in the URL; optional on localhost / private network
            headers["Authorization"] = "Bearer " + ep.token
        headers["User-Agent"] = "crib-monitor"  # some CDNs reject requests with no User-Agent
        # http.client never follows redirects, so a frame cannot bounce to another host.
        if ep.scheme == "https":
            conn = http.client.HTTPSConnection(ep.host, ep.port, timeout=timeout,
                                               context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(ep.host, ep.port, timeout=timeout)
        try:
            try:
                conn.request(method, path, body, headers)
                resp = conn.getresponse()
                if resp.status != 200:
                    resp.read(1024)
                    hint = " (token rejected)" if resp.status in (401, 403) else ""
                    raise RemoteJevError(f"remote jev HTTP {resp.status}{hint}; frame not resent")
                return _read_json(resp)
            except RemoteJevError:
                raise
            except ssl.SSLError as e:  # before ValueError: CertificateError is both, and its text names the host
                raise _remote_error(e) from None
            except ValueError:
                raise  # bad JSON from the server: no host in the message
            except Exception as e:  # OSError, ssl, http.client errors: their text can name the host
                raise _remote_error(e) from None
        finally:
            conn.close()
    key = os.environ.get("CRIB_JEV_KEY", "").strip()
    if key:
        headers["Authorization"] = "Bearer " + key
    # http.client never follows redirects, so a frame cannot bounce to a cloud host.
    conn = http.client.HTTPConnection(ep.host, ep.port, timeout=timeout)
    try:
        conn.request(method, path, body, headers)
        resp = conn.getresponse()
        if resp.status != 200:
            raise RuntimeError(f"jev HTTP {resp.status} refused; frame not resent")
        return _read_json(resp)
    finally:
        conn.close()


def models_path(ep: Endpoint) -> str:
    """'.../systemone' -> '.../models' (so /v1/systemone gives /v1/models); other paths use /v1/models."""
    if ep.kind == "remote" and ep.path.endswith("/systemone"):
        return ep.path[: -len("systemone")] + "models"
    return "/v1/models"


def get_models(ep: Endpoint, timeout: float = 15.0) -> str | None:
    """GET /v1/models through the same client code. Returns the first model id, or None if not reported."""
    out = _exchange(ep, "GET", models_path(ep), None, _timeout_for(ep, timeout))
    items = out.get("data") if isinstance(out.get("data"), list) else out.get("models")
    if isinstance(items, list) and items:
        first = items[0]
        name = first.get("id") or first.get("name") if isinstance(first, dict) else first
        if isinstance(name, str) and name.strip():
            return name.strip()[:80]
    return None


def ask(image, timeout: int = 180, live: bool | None = None) -> dict:
    ep = resolve_endpoint(_LIVE.get() if live is None else live)
    model = os.environ.get("CRIB_JEV_MODEL", "jev-latest").strip() or "jev-latest"
    out = _exchange(ep, "POST", ep.path, build_request(_jpeg_b64(image), model), _timeout_for(ep, timeout))
    assert_saw_image(out)
    answers = out.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("jev response missing answers")
    got = verdict_from_answers(answers)
    if ep.kind == "remote":
        got["backend"] = "remote"  # only this marker goes to tick/score logs, never the URL
    return got


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
