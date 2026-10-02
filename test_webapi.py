"""Self-test for generic web API servers (not only Colab). python test_webapi.py

Plain-http stub on 127.0.0.1 only (no network, no openssl needed). Covers: custom path, token optional on
localhost, token only in the Authorization header (never in the URL), redirects not followed, the models
path follows the systemone path, nothing secret in errors/results/settings text, and the old Colab-style
crib_remote.env / CRIB_JEV_* settings still load and resolve to the same endpoint as before.
"""

from __future__ import annotations

import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

import jev_protocol as J
import remote_settings as R
from test_remote import COVER, TOKEN, env, envc, leak_free


class Api:
    def __init__(self, base="/api/jev", need_token=None):
        self.base, self.need_token, self.mode, self.seen = base, need_token, "ok", []
        api = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                return

            def _go(self):
                n = int(self.headers.get("Content-Length", "0") or 0)
                if n:
                    self.rfile.read(n)
                api.seen.append({"method": self.command, "path": self.path, "auth": self.headers.get("Authorization")})
                if api.mode == "redirect":
                    self.send_response(307)
                    self.send_header("Location", "http://127.0.0.1:1/elsewhere")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if api.need_token and self.headers.get("Authorization") != "Bearer " + api.need_token:
                    return self._reply(401, {})
                if self.command == "GET" and self.path == api.base + "/models":
                    return self._reply(200, {"data": [{"id": "web-jev"}]})
                if self.command == "POST" and self.path == api.base + "/systemone":
                    return self._reply(200, {"answers": COVER, "usage": {"images": [{"w": 8}]}})
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

        self.httpd = Srv(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def web_api_over_http(tmp: Path) -> None:
    img = Image.new("RGB", (8, 8), (240, 240, 240))
    api = Api()
    url = f"http://127.0.0.1:{api.port}/api/jev/systemone"
    try:
        with envc(CRIB_REMOTE_ENV=str(tmp / "none.env"), CRIB_JEV_KEY="local-key-must-not-be-sent"):
            # 1) no token: allowed on localhost, no Authorization header at all
            with env(CRIB_JEV_URL=url, CRIB_JEV_TOKEN=None):
                got = J.ask(img, timeout=10)
                assert got["rule"] == "face_cover" and got["backend"] == "remote"
                assert api.seen[-1]["path"] == "/api/jev/systemone" and api.seen[-1]["auth"] is None
                assert str(api.port) not in json.dumps(got) and "127.0.0.1" not in json.dumps(got)
                assert J.get_models(J.resolve_endpoint(), 5) == "web-jev" and api.seen[-1]["path"] == "/api/jev/models"
                res = R.test_connection(R.load(__import__("os").environ))
                assert res["ok"] and res["backend"] == "remote" and "web-jev" in res["message"]
                assert "127.0.0.1" not in res["message"] and str(api.port) not in res["message"]
            # 2) token given: header only, never in the path/query
            with env(CRIB_JEV_URL=url, CRIB_JEV_TOKEN=TOKEN):
                J.ask(img, timeout=10)
                J.get_models(J.resolve_endpoint(), 5)
                for r in api.seen[-2:]:
                    assert r["auth"] == "Bearer " + TOKEN and TOKEN not in r["path"] and "?" not in r["path"]
                s = R.load(__import__("os").environ)
                assert TOKEN not in repr(s) and TOKEN not in R.status_line(s) and TOKEN not in repr(J.resolve_endpoint())
                assert "127.0.0.1" not in R.status_line(s)
            # 3) server wants a token we do not have: clear error, nothing leaked, no verdict
            api.need_token = "server-side-secret-123"
            with env(CRIB_JEV_URL=url, CRIB_JEV_TOKEN=None):
                try:
                    J.ask(img, timeout=10)
                    raise SystemExit("401 accepted")
                except J.RemoteJevError as e:
                    assert "401" in str(e)
                    leak_free(e, "server-side-secret", "127.0.0.1", str(api.port))
                res = R.test_connection(R.load(__import__("os").environ))
                assert not res["ok"] and "토큰" in res["message"]
            api.need_token = None
            # 4) redirects are never followed (no second request, no verdict)
            api.mode = "redirect"
            n = len(api.seen)
            with env(CRIB_JEV_URL=url, CRIB_JEV_TOKEN=TOKEN):
                try:
                    J.ask(img, timeout=10)
                    raise SystemExit("redirect followed")
                except J.RemoteJevError as e:
                    assert "HTTP 307" in str(e)
                    leak_free(e, TOKEN, "127.0.0.1", str(api.port))
            assert len(api.seen) == n + 1
            api.mode = "ok"
            # 5) wrong path on the server -> 404 error, not a fake verdict
            with env(CRIB_JEV_URL=f"http://127.0.0.1:{api.port}/nope", CRIB_JEV_TOKEN=None):
                try:
                    J.ask(img, timeout=10)
                    raise SystemExit("404 accepted")
                except J.RemoteJevError as e:
                    assert "HTTP 404" in str(e)
        # 6) the server is gone: error (the watch loop turns this into its normal error ticks / push)
        api.close()
        api = None
        with envc(CRIB_REMOTE_ENV=str(tmp / "none.env"), CRIB_JEV_URL=url):
            try:
                J.ask(img, timeout=3)
                raise SystemExit("verdict from a dead server")
            except J.RemoteJevError as e:
                assert "connection failed" in str(e)
    finally:
        if api is not None:
            api.close()
    print("  ok web API over http (custom path, optional token, header only, no redirect, errors)")


def old_colab_settings_still_work(tmp: Path) -> None:
    secret = "ColabTokenValue_abcdef0123456789"
    f = tmp / "crib_remote.env"
    # exactly what the previous version wrote
    f.write_text("# Local only.\nCRIB_JEV_BACKEND=remote\nCRIB_JEV_URL=https://abc-def.trycloudflare.com\n"
                 f"CRIB_JEV_TOKEN={secret}\nCRIB_JEV_TIMEOUT=60\nCRIB_JEV_REMOTE_LIVE=1\n", encoding="utf-8")
    with envc(CRIB_REMOTE_ENV=str(f), CRIB_JEV_BACKEND=None, CRIB_JEV_URL=None, CRIB_JEV_TOKEN=None,
              CRIB_JEV_TIMEOUT=None, CRIB_JEV_REMOTE_LIVE=None):
        s = R.load()
        assert (s.backend, s.url, s.token, s.timeout, s.remote_live) == (
            "remote", "https://abc-def.trycloudflare.com", secret, "60", True)
        assert R.validate(s) == s
        ep = J.resolve_endpoint(live=True)
        assert (ep.kind, ep.scheme, ep.host, ep.port, ep.path, ep.token) == (
            "remote", "https", "abc-def.trycloudflare.com", 443, "/v1/systemone", secret)
        assert J.models_path(ep) == "/v1/models" and J.remote_live_active()
        assert J._timeout_for(ep, 180) == 60.0
        assert secret not in repr(ep) and secret not in repr(s) and "abc-def" not in R.status_line(s)
        # saving the same settings again writes the same address (default path left out)
        R.save(s, tmp / "again.env")
        assert R.load({"CRIB_REMOTE_ENV": str(tmp / "again.env")}, tmp / "again.env") == s
    # env-only, the way the README documents it
    with envc(CRIB_REMOTE_ENV=str(tmp / "none.env"), CRIB_JEV_URL="https://abc-def.trycloudflare.com",
              CRIB_JEV_TOKEN=secret):
        assert J.resolve_endpoint().token == secret
    print("  ok old Colab settings (crib_remote.env, CRIB_JEV_URL/TOKEN/TIMEOUT/REMOTE_LIVE) unchanged")


def local_default_untouched(tmp: Path) -> None:
    with envc(CRIB_REMOTE_ENV=str(tmp / "none.env"), CRIB_JEV_URL=None, CRIB_JEV_TOKEN=None, CRIB_JEV_BACKEND=None):
        ep = J.resolve_endpoint()
        assert (ep.kind, ep.scheme if ep.kind == "remote" else "-", ep.host, ep.port, ep.path) == (
            "local", "-", "127.0.0.1", 8090, "/v1/systemone")
    print("  ok default stays local loopback")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        local_default_untouched(tmp)
        old_colab_settings_still_work(tmp)
        web_api_over_http(tmp)
    print("ok webapi")


if __name__ == "__main__":
    main()
