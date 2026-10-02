"""Token-checking reverse proxy for the Colab Jev server. Stdlib only. Not a medical device.

Listens on 127.0.0.1:8091 and forwards ONLY
  POST /v1/systemone  and  GET /v1/models
to the imajev server on 127.0.0.1:8090, and only when the request has
  Authorization: Bearer <token>
(constant-time compare). Everything else is refused: 401 without/with a wrong token (checked first,
so strangers learn nothing), then 404 for other paths, 405 for a wrong method, 411/413 for a missing
or oversized body (cap 25 MiB). The token is not forwarded and never logged.

  GATEWAY_TOKEN=<token> python colab/gateway.py [--port 8091] [--backend-port 8090]
  python colab/gateway.py --selftest
"""

from __future__ import annotations

import argparse
import http.client
import os
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 25 * 1024 * 1024
MAX_REPLY = 8 * 1024 * 1024
ROUTES = {("POST", "/v1/systemone"), ("GET", "/v1/models")}
KNOWN_PATHS = {p for _, p in ROUTES}
BACKEND_TIMEOUT = 300


def token_ok(header: str | None, token: str) -> bool:
    """True if header is 'Bearer <token>'. Constant-time on the token bytes; never raises."""
    if not token or not header:
        return False
    scheme, _, given = header.strip().partition(" ")
    if scheme.lower() != "bearer":
        return False
    return secrets.compare_digest(given.strip().encode("utf-8"), token.encode("utf-8"))


def make_server(token: str, backend_port: int = 8090, host: str = "127.0.0.1", port: int = 8091,
                max_body: int = MAX_BODY) -> ThreadingHTTPServer:
    if not token:
        raise ValueError("empty token")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 120  # a stalled client cannot hold a thread forever
        server_version = "gw"
        sys_version = ""

        def log_message(self, fmt, *args):  # never log headers or bodies
            sys.stderr.write(f"gateway {self.command} {self.path.split('?')[0][:40]} -> {getattr(self, '_code', '-')}\n")

        def _send(self, code: int, body: bytes = b"", ctype: str = "application/json") -> None:
            self._code = code
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            self.close_connection = True

        def _err(self, code: int, text: str) -> None:
            self._send(code, ('{"error":"%s"}' % text).encode())

        def _handle(self) -> None:
            if not token_ok(self.headers.get("Authorization"), token):
                return self._err(401, "unauthorized")
            path = self.path.split("?")[0]
            if (self.command, path) not in ROUTES:
                return self._err(404 if path not in KNOWN_PATHS else 405, "not found" if path not in KNOWN_PATHS else "method not allowed")
            if "?" in self.path:
                return self._err(404, "not found")
            body = None
            if self.command == "POST":
                if self.headers.get("Transfer-Encoding"):
                    return self._err(411, "length required")
                raw = self.headers.get("Content-Length")
                try:
                    n = int(raw) if raw is not None else -1
                except ValueError:
                    n = -1
                if n < 0:
                    return self._err(411, "length required")
                if n > max_body:
                    return self._err(413, "body too large")
                body = self.rfile.read(n)
                if len(body) != n:
                    return self._err(400, "short body")
            headers = {}
            if body is not None:
                headers["Content-Type"] = self.headers.get("Content-Type", "application/json")
            conn = http.client.HTTPConnection("127.0.0.1", backend_port, timeout=BACKEND_TIMEOUT)
            try:
                conn.request(self.command, path, body, headers)  # Authorization is NOT forwarded
                resp = conn.getresponse()
                data = resp.read(MAX_REPLY + 1)
                if len(data) > MAX_REPLY:
                    return self._err(502, "backend reply too large")
                self._send(resp.status, data, resp.getheader("Content-Type", "application/json"))
            except (OSError, http.client.HTTPException):
                self._err(502, "backend unavailable")
            finally:
                conn.close()

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _handle

    class Server(ThreadingHTTPServer):
        daemon_threads = True

    return Server((host, port), Handler)


def _selftest() -> None:
    import json
    import urllib.error
    import urllib.request

    seen: list[dict] = []

    class Backend(BaseHTTPRequestHandler):
        def log_message(self, *a):
            return

        def _go(self):
            n = int(self.headers.get("Content-Length", "0") or 0)
            data = self.rfile.read(n) if n else b""
            seen.append({"m": self.command, "p": self.path, "auth": self.headers.get("Authorization"), "len": len(data)})
            out = json.dumps({"ok": True, "echo": len(data), "data": [{"id": "stub"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        do_GET = do_POST = _go

    backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    token = secrets.token_urlsafe(32)
    gw = make_server(token, backend.server_address[1], port=0, max_body=1024)
    threading.Thread(target=gw.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{gw.server_address[1]}"

    def call(method, path, body=None, auth=None, headers=None):
        h = dict(headers or {})
        if auth is not None:
            h["Authorization"] = auth
        req = urllib.request.Request(base + path, data=body, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    err = sys.stderr
    sys.stderr = open(os.devnull, "w")  # keep the self-test output clean
    try:
        good = "Bearer " + token
        payload = b'{"images":["x"]}'
        # 401: no header, wrong token, wrong scheme, empty bearer, near-miss; nothing reaches the backend
        for auth in (None, "Bearer wrong", "Bearer " + token[:-1], "Bearer " + token + "x", "Basic " + token,
                     "Bearer ", token, "Bearer \u00e9"):
            for method, path in (("POST", "/v1/systemone"), ("GET", "/v1/models"), ("GET", "/other")):
                code, _ = call(method, path, payload if method == "POST" else None, auth)
                assert code == 401, (auth, method, path, code)
        assert not seen
        # 401 even for an oversized body (auth is checked first)
        assert call("POST", "/v1/systemone", b"x" * 5000, "Bearer wrong")[0] == 401 and not seen
        # 200 with the right token, both routes, token not forwarded, body intact
        code, body = call("POST", "/v1/systemone", payload, good, {"Content-Type": "application/json"})
        assert code == 200 and json.loads(body)["echo"] == len(payload)
        code, body = call("GET", "/v1/models", None, good)
        assert code == 200 and json.loads(body)["data"][0]["id"] == "stub"
        assert [s["p"] for s in seen] == ["/v1/systemone", "/v1/models"]
        assert all(s["auth"] is None for s in seen), "token was forwarded to the backend"
        assert call("POST", "/v1/systemone", payload, "bearer " + token)[0] == 200  # scheme is case-insensitive
        n = len(seen)
        # 404 other paths, 405 wrong methods, no query strings, nothing forwarded
        for method, path in (("GET", "/"), ("GET", "/docs"), ("POST", "/v1/chat/completions"), ("GET", "/v1/systemone/x"),
                             ("POST", "/v1/models/x"), ("GET", "/v1/models?x=1"), ("GET", "/../etc/passwd")):
            assert call(method, path, b"{}" if method == "POST" else None, good)[0] == 404, (method, path)
        assert call("GET", "/v1/systemone", None, good)[0] == 405
        assert call("POST", "/v1/models", b"{}", good)[0] == 405
        for method in ("PUT", "DELETE", "PATCH"):
            assert call(method, "/v1/systemone", b"{}", good)[0] == 405
        assert len(seen) == n
        # body cap: <= cap passes, > cap is 413 and never forwarded; missing length is 411
        assert call("POST", "/v1/systemone", b"x" * 1024, good)[0] == 200
        n = len(seen)
        assert call("POST", "/v1/systemone", b"x" * 1025, good)[0] == 413 and len(seen) == n
        import socket

        with socket.create_connection(("127.0.0.1", gw.server_address[1]), timeout=5) as s:  # huge claim, no body sent
            s.sendall(f"POST /v1/systemone HTTP/1.1\r\nHost: x\r\nAuthorization: {good}\r\nContent-Length: {MAX_BODY + 1}\r\n\r\n".encode())
            assert s.recv(100).startswith(b"HTTP/1.1 413")
        with socket.create_connection(("127.0.0.1", gw.server_address[1]), timeout=5) as s:
            s.sendall(f"POST /v1/systemone HTTP/1.1\r\nHost: x\r\nAuthorization: {good}\r\nTransfer-Encoding: chunked\r\n\r\n".encode())
            assert s.recv(100).startswith(b"HTTP/1.1 411")
        assert len(seen) == n
        assert MAX_BODY == 25 * 1024 * 1024
        # backend down -> 502, not a hang
        backend.shutdown()
        backend.server_close()
        assert call("GET", "/v1/models", None, good)[0] == 502
        # constant-time helper and empty-token refusal
        assert token_ok(good, token) and not token_ok(good, "") and not token_ok(None, token)
        try:
            make_server("", port=0)
            raise SystemExit("empty token accepted")
        except ValueError:
            pass
        assert gw.server_address[0] == "127.0.0.1"
    finally:
        sys.stderr = err
        gw.shutdown()
        gw.server_close()
    print("ok colab-gateway")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8091)
    ap.add_argument("--backend-port", type=int, default=8090)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        _selftest()
        return 0
    token = os.environ.get("GATEWAY_TOKEN", "").strip()  # env, not argv: not visible in `ps`
    if len(token) < 16:
        print("set GATEWAY_TOKEN (at least 16 characters, e.g. secrets.token_urlsafe(32))", file=sys.stderr)
        return 2
    srv = make_server(token, a.backend_port, port=a.port)
    print(f"gateway on 127.0.0.1:{a.port} -> 127.0.0.1:{a.backend_port} (token required)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
