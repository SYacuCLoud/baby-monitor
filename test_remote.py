"""Self-test for the opt-in remote Jev mode. python test_remote.py

Local stub servers only (an https stub needs the `openssl` CLI to make a throwaway certificate; without
it the TLS cases are skipped and say so). Covers: https + token required, http refused, Authorization
header, token/host never in errors or logs, live context stays local, score_folder --remote.
The live-watch opt-in and '판정 불가' push are tested in test_remote_live.py.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

import jev_protocol as J
import remote_settings as R

TOKEN = "tkn_Zx81QvLmN0pRsT4uVwYz_secret-VALUE"
COVER = {
    "baby_present": {"type": "noul", "noul": 0.96}, "adult_present": {"noul": 0.02},
    "face_visible": {"noul": 0.10}, "face_cover": {"noul": 0.91},
    "face_down_cheek": {"noul": 0.04}, "face_down_back": {"noul": 0.02},
    "face_down_belly": {"noul": 0.03}, "climbing": {"noul": 0.03},
}
QUIET = {k: {"noul": 0.5} for k in COVER}


class Stub:
    """One server. mode: ok | drop | slow | redirect | http500. Records requests."""

    def __init__(self, ctx=None, token=TOKEN, answers=None):
        self.mode, self.token, self.answers = "ok", token, answers or COVER
        self.seen: list[dict] = []
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                return

            def _go(self):
                n = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(n) if n else b""
                stub.seen.append({"method": self.command, "path": self.path,
                                  "auth": self.headers.get("Authorization"), "body": raw})
                if stub.mode == "drop":
                    self.connection.close()  # no response at all
                    self.close_connection = True
                    return
                if stub.mode == "slow":
                    time.sleep(3)
                if stub.mode == "redirect":
                    self.send_response(307)
                    self.send_header("Location", "https://api.typesafe.ai/v1/systemone")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if stub.token is not None and self.headers.get("Authorization") != "Bearer " + stub.token:
                    return self._reply(401, {"error": "unauthorized"})
                if stub.mode == "http500":
                    return self._reply(500, {"error": "boom"})
                if self.command == "GET" and self.path == "/v1/models":
                    return self._reply(200, {"data": [{"id": "imajev-4b"}]})
                if self.command == "POST" and self.path == "/v1/systemone":
                    return self._reply(200, {"answers": stub.answers, "usage": {"images": [{"w": 8}]}})
                self._reply(404, {})

            do_GET = do_POST = _go

            def _reply(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class Srv(ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):  # a dropped/aborted TLS client is expected
                pass

        self.httpd = Srv(("127.0.0.1", 0), H)
        if ctx is not None:
            self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def make_cert(tmp: Path):
    if not shutil.which("openssl"):
        return None
    cert, key = tmp / "c.pem", tmp / "k.pem"
    r = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
         "-days", "2", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
        capture_output=True)
    if r.returncode != 0:
        return None
    return cert, key


@contextlib.contextmanager
def env(**kw):
    saved = {k: os.environ.get(k) for k in kw}
    for k, v in kw.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def envc(**kw):
    return env(**{**CLEAN, **kw})


def envd(base, **kw):
    return env(**{**base, **kw})


CLEAN = {k: None for k in ("CRIB_JEV_URL", "CRIB_JEV_TOKEN", "CRIB_JEV_TIMEOUT", "CRIB_JEV_REMOTE_LIVE",
                           "CRIB_JEV_BACKEND", "CRIB_JEV_KEY", "CRIB_MODEL", "SSL_CERT_FILE")}


def leak_free(exc: BaseException, *secrets: str) -> None:
    blob = "\n".join([str(exc), repr(exc), "".join(traceback.format_exception(exc))])
    for s in secrets:
        assert s not in blob, f"leaked {s[:6]}... in {blob[:200]}"


def rules_without_network(tmp: Path) -> None:
    img = Image.new("RGB", (8, 8), (240, 240, 240))
    nofile = str(tmp / "none.env")
    with envc(CRIB_REMOTE_ENV=nofile):
        # default: untouched local loopback
        ep = J.resolve_endpoint()
        assert (ep.kind, ep.host, ep.port, ep.path) == ("local", "127.0.0.1", 8090, "/v1/systemone")
        assert J.resolve_endpoint(live=True).kind == "local"
        # https without a token: refused before any network (the host does not resolve anyway)
        with env(CRIB_JEV_URL="https://abc-def.invalid"):
            try:
                J.ask(img)
                raise SystemExit("https without token accepted")
            except J.RemoteJevError as e:
                assert not isinstance(e, SystemExit) and "invalid" in str(e)
                leak_free(e, "abc-def")
        # plain http to anything but localhost / a private-network IP is refused (also with a token)
        for url in ("http://abc-def.invalid/v1/systemone", "http://8.8.8.8", "http://abc-def.invalid",
                    "http://localhost.evil.com:8090", "http://192.168.1.1.evil.com", "http://10.evil.com",
                    "http://user@evil.com@192.168.1.1/", "http://100.64.0.1"):
            with env(CRIB_JEV_URL=url, CRIB_JEV_TOKEN=TOKEN):
                try:
                    J.resolve_endpoint()
                    raise SystemExit(f"accepted {url}")
                except J.RemoteJevError as e:
                    assert not isinstance(e, SystemExit) and TOKEN not in str(e)
                    leak_free(e, "abc-def", "evil", "8.8.8.8", "192.168", "100.64", TOKEN)
        # bad remote urls
        for url in ("https://u:p@abc.example.com", "https://abc.example.com/a/../b", "https://abc.example.com/?a=1",
                    "https://abc.example.com/a%2fb", "https://abc.example.com:0"):
            with env(CRIB_JEV_URL=url, CRIB_JEV_TOKEN=TOKEN):
                try:
                    J.resolve_endpoint()
                    raise SystemExit(f"accepted {url}")
                except J.RemoteJevError as e:
                    leak_free(e, "abc.example", "u:p", TOKEN)
        # any path is accepted; https always needs a token
        with env(CRIB_JEV_URL="https://abc.example.com/api/jev/systemone", CRIB_JEV_TOKEN=TOKEN):
            ep = J.resolve_endpoint()
            assert (ep.kind, ep.scheme, ep.port, ep.path, ep.token) == ("remote", "https", 443, "/api/jev/systemone", TOKEN)
            assert J.models_path(ep) == "/api/jev/models" and TOKEN not in repr(ep) and "abc" not in repr(ep)
        with env(CRIB_JEV_URL="https://abc.example.com/x", CRIB_JEV_TOKEN=None):
            try:
                J.resolve_endpoint()
                raise SystemExit("https custom path without token accepted")
            except J.RemoteJevError:
                pass
        # localhost / private network: http and no token are fine; a given token is still sent
        for url, host in (("http://localhost:9000/api", "localhost"), ("http://192.168.1.20:9000", "192.168.1.20"),
                          ("http://[::1]:9000/a/b", "::1"), ("http://10.0.0.5/v1/systemone", "10.0.0.5"),
                          ("https://192.168.1.20", "192.168.1.20")):
            with env(CRIB_JEV_URL=url, CRIB_JEV_TOKEN=None):
                ep = J.resolve_endpoint()
                assert ep.kind == "remote" and ep.host == host and ep.token == "", url
            with env(CRIB_JEV_URL=url, CRIB_JEV_TOKEN=TOKEN):
                assert J.resolve_endpoint().token == TOKEN
        # a token in the env with the plain legacy local url changes nothing: still local, no token sent
        with env(CRIB_JEV_URL="http://127.0.0.1:8090/v1/systemone", CRIB_JEV_TOKEN=TOKEN):
            ep = J.resolve_endpoint()
            assert ep.kind == "local" and ep.token == ""
        # https + token: remote for tests/scoring, local for live
        with env(CRIB_JEV_URL="https://abc-def.trycloudflare.com", CRIB_JEV_TOKEN=TOKEN):
            ep = J.resolve_endpoint(live=False)
            assert ep.kind == "remote" and ep.host == "abc-def.trycloudflare.com" and ep.port == 443
            assert TOKEN not in repr(ep) and "abc-def" not in repr(ep) and "***.trycloudflare.com" in repr(ep)
            with contextlib.redirect_stderr(io.StringIO()) as err:
                live = J.resolve_endpoint(live=True)
            assert live.kind == "local" and (live.host, live.port) == ("127.0.0.1", 8090)
            assert "abc-def" not in err.getvalue() and TOKEN not in err.getvalue()
            with J.live_context(), contextlib.redirect_stderr(io.StringIO()):
                assert J.resolve_endpoint(live=True).kind == "local"
            # timeout: default is the caller's; CRIB_JEV_TIMEOUT only for remote
            assert J._timeout_for(ep, 180) == 180
            with env(CRIB_JEV_TIMEOUT="45"):
                assert J._timeout_for(ep, 180) == 45.0
                assert J._timeout_for(J.resolve_endpoint.__globals__["Endpoint"]("local", "127.0.0.1", 8090, "/x"), 180) == 180
            with env(CRIB_JEV_TIMEOUT="abc"):
                try:
                    J._timeout_for(ep, 180)
                    raise SystemExit("bad timeout accepted")
                except J.RemoteJevError:
                    pass
    print("  ok remote rules (no network)")


def tls_cases(tmp: Path, certs) -> None:
    cert, key = certs
    srv_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    srv_ctx.load_cert_chain(str(cert), str(key))
    img = Image.new("RGB", (8, 8), (240, 240, 240))
    stub = Stub(srv_ctx)
    url = f"https://localhost:{stub.port}"
    try:
        with envc(CRIB_REMOTE_ENV=str(tmp / "none.env"), SSL_CERT_FILE=str(cert),
                 CRIB_JEV_URL=url, CRIB_JEV_TOKEN=TOKEN, CRIB_JEV_KEY="local-key-should-not-be-sent"):
            got = J.ask(img, timeout=10)
            assert got["rule"] == "face_cover" and got["should_alert"] is True and got["backend"] == "remote"
            req = stub.seen[-1]
            assert req["path"] == "/v1/systemone" and req["auth"] == "Bearer " + TOKEN  # header sent, local key not
            body = json.loads(req["body"])
            assert body["images"][0].startswith("data:image/jpeg;base64,") and isinstance(body["questions"], dict)
            assert json.dumps(got).find(TOKEN) < 0 and "localhost" not in json.dumps(got)
            # same request shape as local mode (questions/state untouched)
            assert body["questions"] == J.questions() and body["state"] == {"kind": "crib_frame", "image": "attached"}
            # GET /v1/models through the same client
            ep = J.resolve_endpoint()
            assert J.get_models(ep, 5) == "imajev-4b" and stub.seen[-1]["method"] == "GET"
            assert stub.seen[-1]["auth"] == "Bearer " + TOKEN
            res = R.test_connection(R.load(os.environ))
            assert res["ok"] and res["backend"] == "remote" and res["model"] == "imajev-4b" and "imajev-4b" in res["message"]
            assert TOKEN not in json.dumps(res) and "localhost" not in res["message"]

            # wrong token -> 401, no fake verdict, no leak
            with env(CRIB_JEV_TOKEN="wrong_token_value_123"):
                try:
                    J.ask(img, timeout=10)
                    raise SystemExit("401 accepted")
                except J.RemoteJevError as e:
                    assert "401" in str(e) and not isinstance(e, SystemExit)
                    leak_free(e, "wrong_token_value_123", TOKEN, "localhost", str(stub.port))
                res = R.test_connection(R.load(os.environ))
                assert not res["ok"] and "토큰" in res["message"] and "wrong_token" not in res["message"]
            # server error / redirect / dropped connection / not trusted cert / slow
            for mode, want in (("http500", "HTTP 500"), ("redirect", "HTTP 307"), ("drop", "connection failed")):
                stub.mode = mode
                n = len(stub.seen)
                try:
                    J.ask(img, timeout=10)
                    raise SystemExit(f"{mode}: got a verdict")
                except J.RemoteJevError as e:
                    assert want in str(e), (mode, str(e))
                    leak_free(e, TOKEN, "localhost", str(stub.port))
                if mode == "redirect":
                    assert len(stub.seen) == n + 1  # not followed
            stub.mode = "slow"
            with env(CRIB_JEV_TIMEOUT="1"):
                t0 = time.time()
                try:
                    J.ask(img, timeout=180)
                    raise SystemExit("slow got a verdict")
                except J.RemoteJevError as e:
                    assert "timeout" in str(e) and time.time() - t0 < 2.9
                    leak_free(e, TOKEN, "localhost")
            stub.mode = "ok"
            with env(SSL_CERT_FILE=None):  # self-signed cert is not trusted by the system store
                try:
                    J.ask(img, timeout=10)
                    raise SystemExit("untrusted cert accepted")
                except J.RemoteJevError as e:
                    assert "tls" in str(e)
                    leak_free(e, TOKEN, "localhost")
            # plain http to a non-local name must be refused client-side (no request sent)
            n = len(stub.seen)
            with env(CRIB_JEV_URL=f"http://localhost.evil.com:{stub.port}"):
                try:
                    J.ask(img, timeout=5)
                    raise SystemExit("http remote accepted")
                except J.RemoteJevError as e:
                    leak_free(e, "evil", TOKEN)
            assert len(stub.seen) == n
    finally:
        stub.close()
    print("  ok remote client over TLS stub (header, 401, 500, redirect, drop, timeout, untrusted cert)")


def score_folder_cases(tmp: Path, certs) -> None:
    import score_folder as SF

    cert, key = certs
    srv_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    srv_ctx.load_cert_chain(str(cert), str(key))
    stub = Stub(srv_ctx, answers=QUIET)
    folder = tmp / "photos"
    folder.mkdir()
    for i in range(2):
        Image.new("RGB", (8, 8), (100, 100, 100)).save(folder / f"normal_{i}.jpg")
    cfg = tmp / "crib_remote.env"
    R.save(R.Settings("remote", f"https://localhost:{stub.port}", TOKEN, "", False), cfg)
    try:
        with envc(CRIB_REMOTE_ENV=str(cfg), SSL_CERT_FILE=str(cert)):
            # default: the file is ignored -> local 8090 (down) -> every photo fails, remote untouched
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stderr(err):
                code = SF.run(folder, "jev", None, out=out)
            assert code == 1 and not stub.seen and "backend: remote" not in out.getvalue()
            # --remote: uses the file's remote server and says so (masked)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stderr(err):
                code = SF.run(folder, "jev", None, out=out, remote=True)
            text = out.getvalue()
            assert code == 0, (text, err.getvalue())
            assert "backend: remote (***.localhost" not in text and "backend: remote (***" in text
            assert len(stub.seen) == 2 and all(r["auth"] == "Bearer " + TOKEN for r in stub.seen)
            assert TOKEN not in text + err.getvalue() and f":{stub.port}" not in text
            assert os.environ.get("CRIB_JEV_BACKEND") is None  # restored
            # --remote without settings -> clear exit 2
            with env(CRIB_REMOTE_ENV=str(tmp / "absent.env")):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    assert SF.run(folder, "jev", None, out=io.StringIO(), remote=True) == 2
                assert "--remote needs" in err.getvalue()
            # remote down: rows are errors, never a safe-looking score; message has no token/host
            stub.mode = "drop"
            out = io.StringIO()
            with contextlib.redirect_stderr(io.StringIO()):
                code = SF.run(folder, "jev", None, out=out, remote=True)
            assert code == 1 and "ERR" in out.getvalue() and TOKEN not in out.getvalue()
            assert "error  normal_0.jpg: RemoteJevError: remote jev connection failed" in out.getvalue()
    finally:
        stub.close()
    print("  ok score_folder: default unchanged, --remote uses remote, errors are rows")


def main() -> None:
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        rules_without_network(tmp)
        certs = make_cert(tmp)
        if certs is None:
            print("  SKIPPED TLS stub cases: need the `openssl` command to make a test certificate")
        else:
            tls_cases(tmp, certs)
            score_folder_cases(tmp, certs)
    print("ok remote")


if __name__ == "__main__":
    main()
