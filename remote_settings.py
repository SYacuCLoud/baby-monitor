"""Remote Jev server settings: load / save / validate / mask. Pure stdlib, no tkinter.

Remote mode is OPT IN. Nothing here changes the default (local loopback Jev only).

Settings come from environment variables and the optional file crib_remote.env (gitignored, 0600
best effort). Env overrides the file. The file only counts when its CRIB_JEV_BACKEND is 'remote'
(or env CRIB_JEV_BACKEND=remote); env CRIB_JEV_BACKEND=local ignores the file.

  CRIB_JEV_BACKEND      local (default) | remote
  CRIB_JEV_URL          address of any Jev-compatible web API, with or without a path
                        (default path /v1/systemone). No credentials, ?, # or ; in it.
                          localhost / private network (10.x, 172.16-31.x, 192.168.x, fe80::, fc00::/7):
                            http or https, token optional
                          anything else (every host NAME except 'localhost', public IPs):
                            https AND a token are required
  CRIB_JEV_TOKEN        bearer token, sent only in the Authorization header. Colab prints one.
  CRIB_JEV_TIMEOUT      seconds per request (remote only), default = caller's timeout (180)
  CRIB_JEV_REMOTE_LIVE  1 = live watch may use remote too. Default 0: live watch stays local.

The token and the full URL are never put into error text or logs. Use mask_host().
Self-test: python remote_settings.py
"""

from __future__ import annotations

import ipaddress
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

DIR = Path(__file__).resolve().parent
ENV_FILE = DIR / "crib_remote.env"
KEYS = ("CRIB_JEV_BACKEND", "CRIB_JEV_URL", "CRIB_JEV_TOKEN", "CRIB_JEV_TIMEOUT", "CRIB_JEV_REMOTE_LIVE")
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
DEFAULT_PATH = "/v1/systemone"
_PRIVATE_NETS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "fc00::/7", "fe80::/10"))
_PATH_RE = re.compile(r"^/[A-Za-z0-9._~:+/-]*$")
_TRUE = frozenset({"1", "true", "yes", "on"})
_TOKEN_RE = re.compile(r"^[\x21-\x7e]{1,512}$")  # printable ASCII, no spaces: safe as a header value
MAX_TIMEOUT = 3600.0


class SettingsError(ValueError):
    """Invalid remote settings. The message never contains the token or the URL."""


def env_file_path(environ=None) -> Path:
    environ = os.environ if environ is None else environ
    raw = (environ.get("CRIB_REMOTE_ENV") or "").strip()
    return Path(raw) if raw else ENV_FILE


def parse_env(path: Path) -> dict[str, str]:
    """Same format as ntfy.env / rtsp.env: KEY=VALUE lines, # comments."""
    out: dict[str, str] = {}
    try:
        if not path.is_file():
            return out
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def mask_host(url_or_host: str) -> str:
    """'abc-def.trycloudflare.com' -> '***.trycloudflare.com'. Never returns the full host."""
    raw = (url_or_host or "").strip()
    try:
        host = (urlparse(raw).hostname if "://" in raw else raw.split("/")[0].split(":")[0]) or ""
    except ValueError:
        return "***"
    if _ip_literal(host) is not None:  # an IP address has no domain part that is safe to show
        return "***(IP 주소)"
    labels = [p for p in host.lower().split(".") if p]
    if len(labels) >= 3:
        return "***." + ".".join(labels[-2:])
    if len(labels) == 2:
        return "***." + labels[-1]
    return "***"


def is_loopback_host(host: str | None) -> bool:
    return (host or "").lower() in LOOPBACK


def _ip_literal(host: str):
    """IPv4/IPv6 address object for a literal (brackets and zone ids not allowed), else None.
    Only the strict dotted form counts: '127.1' and '0x7f.1' are NOT literals, so they are host names."""
    h = (host or "").strip()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if "%" in h:
        return None
    try:
        return ipaddress.ip_address(h)
    except ValueError:
        return None


def host_scope(host: str | None) -> str:
    """'loopback' | 'private' | 'public'. Only the literal name 'localhost' and real IP literals can be
    loopback or private; every other host name ('localhost.evil.com', '10.evil.com', 'nas.local') is public."""
    h = (host or "").strip().lower()
    if h == "localhost":
        return "loopback"
    ip = _ip_literal(h)
    if ip is None:
        return "public"
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:10.0.0.1 is judged as 10.0.0.1
    if ip.is_loopback:
        return "loopback"
    if any(ip in net for net in _PRIVATE_NETS if net.version == ip.version):
        return "private"
    return "public"


@dataclass(frozen=True)
class ParsedUrl:
    scheme: str   # 'http' | 'https'
    host: str     # lowercase, no brackets
    port: int
    path: str     # always starts with '/', no trailing '/'

    @property
    def scope(self) -> str:
        return host_scope(self.host)

    @property
    def token_required(self) -> bool:
        return self.scope == "public"


def is_remote_url(url: str) -> bool:
    """True unless it is the plain legacy local form: http, loopback host, path /v1/systemone.
    (Legacy local keeps its old behaviour: no token, CRIB_JEV_KEY, and strict refusals.)"""
    try:
        p = urlparse((url or "").strip())
        return not (p.scheme == "http" and is_loopback_host(p.hostname) and p.path.rstrip("/") == DEFAULT_PATH)
    except ValueError:
        return True


def parse_url(url: str) -> ParsedUrl:
    """Validate a Jev server address. Raises SettingsError (message never contains the address)."""
    raw = (url or "").strip()
    if not raw:
        raise SettingsError("서버 주소가 비어 있습니다.")
    if any(ord(c) <= 0x20 or ord(c) >= 0x7F or c in "\\%" for c in raw):
        raise SettingsError("서버 주소에 공백, 제어문자, 한글, %, 역슬래시를 넣을 수 없습니다.")
    try:
        p = urlparse(raw)
        port = p.port
        host = (p.hostname or "").lower()
    except ValueError:
        raise SettingsError("서버 주소 형식이 올바르지 않습니다.") from None
    if p.scheme not in ("http", "https"):
        raise SettingsError("서버 주소는 http:// 또는 https://로 시작해야 합니다.")
    if not host or not p.netloc:
        raise SettingsError("서버 주소에 호스트가 없습니다.")
    if "@" in p.netloc or p.username or p.password:
        raise SettingsError("서버 주소에 아이디/비밀번호(@)를 넣지 마세요. 토큰은 따로 입력합니다.")
    if p.query or p.fragment or p.params or "?" in raw or "#" in raw or ";" in raw:
        raise SettingsError("서버 주소에 ?, #, ; 부분을 넣지 마세요.")
    if port == 0:
        raise SettingsError("서버 주소의 포트가 올바르지 않습니다.")
    path = p.path.rstrip("/")
    if path:
        segs = path.split("/")[1:]
        if (not _PATH_RE.match(path) or len(path) > 200 or any(sg in ("", ".", "..") for sg in segs)):
            raise SettingsError("서버 주소의 경로가 올바르지 않습니다 (영문, 숫자, . _ ~ : + - / 만, .. 불가).")
    else:
        path = DEFAULT_PATH
    if p.scheme == "http" and host_scope(host) == "public":
        raise SettingsError(
            "http는 이 PC(localhost)나 같은 네트워크의 사설 IP 주소에서만 허용합니다. "
            "그 밖의 주소는 https와 토큰이 필요합니다.")
    return ParsedUrl(p.scheme, host, port or (443 if p.scheme == "https" else 80), path)


def validate_url(url: str) -> str:
    """Return the normalized address 'scheme://host[:port][path]' (default path is left out)."""
    u = parse_url(url)
    host = f"[{u.host}]" if ":" in u.host else u.host
    default_port = 443 if u.scheme == "https" else 80
    return (f"{u.scheme}://{host}" + (f":{u.port}" if u.port != default_port else "")
            + ("" if u.path == DEFAULT_PATH else u.path))


def url_scope(url: str) -> str:
    """Scope of a configured address; anything unparsable counts as 'public' (the careful answer)."""
    try:
        return parse_url(url).scope
    except SettingsError:
        return "public"


_DEST = {"public": "사진이 집 밖으로 나갑니다",
         "private": "사진이 같은 네트워크의 다른 기기로 나갑니다",
         "loopback": "사진은 이 PC 안의 다른 서버로 갑니다"}


def destination_for_scope(scope: str) -> str:
    return _DEST.get(scope, _DEST["public"])


def photo_destination(url: str) -> str:
    """One Korean sentence (no address in it) saying where photos go for this server address."""
    return destination_for_scope(url_scope(url))


def validate_token(token: str, required: bool = True) -> str:
    tok = (token or "").strip()
    if not tok:
        if required:
            raise SettingsError("토큰이 비어 있습니다. 이 주소는 https와 토큰이 필요합니다.")
        return ""
    if not _TOKEN_RE.match(tok):
        raise SettingsError("토큰에 공백이나 보이지 않는 문자가 있습니다.")
    return tok


def parse_timeout(raw: str | None) -> float | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        raise SettingsError("CRIB_JEV_TIMEOUT은 초 단위 숫자여야 합니다.") from None
    if not (0.0 < v <= MAX_TIMEOUT):  # also rejects nan
        raise SettingsError(f"CRIB_JEV_TIMEOUT은 0 초과 {int(MAX_TIMEOUT)} 이하여야 합니다.")
    return v


@dataclass(frozen=True)
class Settings:
    backend: str = "local"       # 'local' | 'remote'
    url: str = ""
    token: str = ""
    timeout: str = ""
    remote_live: bool = False

    def __repr__(self) -> str:  # never show the token or URL
        return (f"Settings(backend={self.backend!r}, host={mask_host(self.url)!r}, "
                f"token={'set' if self.token else 'unset'}, remote_live={self.remote_live})")

    @property
    def remote_url_configured(self) -> bool:
        return bool(self.url) and is_remote_url(self.url)


def load(environ=None, path: Path | None = None) -> Settings:
    """Merge env over file. File values count only when the effective backend is 'remote'."""
    environ = os.environ if environ is None else environ
    path = path or env_file_path(environ)

    def env(key: str) -> str | None:
        v = (environ.get(key) or "").strip()
        return v or None

    file_vals = parse_env(path)
    backend_env = (env("CRIB_JEV_BACKEND") or "").lower()
    backend_file = (file_vals.get("CRIB_JEV_BACKEND") or "").strip().lower()
    use_file = (backend_env or backend_file) == "remote"
    backend = backend_env or backend_file or "local"
    if backend not in ("local", "remote"):
        backend = "local"

    def pick(key: str) -> str:
        v = env(key)
        if v is None and use_file:
            v = (file_vals.get(key) or "").strip() or None
        return v or ""

    return Settings(
        backend=backend,
        url=pick("CRIB_JEV_URL"),
        token=pick("CRIB_JEV_TOKEN"),
        timeout=pick("CRIB_JEV_TIMEOUT"),
        remote_live=pick("CRIB_JEV_REMOTE_LIVE").lower() in _TRUE,
    )


def validate(s: Settings) -> Settings:
    """Check a remote Settings. Returns it with a normalized url. Raises SettingsError."""
    parse_timeout(s.timeout)
    u = parse_url(s.url)
    return Settings(s.backend, validate_url(s.url), validate_token(s.token, u.token_required),
                    s.timeout.strip(), s.remote_live)


def settings_from_form(backend: str, url: str, token: str, timeout: str = "", live: bool = False) -> Settings:
    """GUI form values -> Settings (stripped, not validated). Anything but 'remote' means local."""
    return Settings("remote" if backend == "remote" else "local", (url or "").strip(), (token or "").strip(),
                    (timeout or "").strip(), bool(live))


def env_overrides(environ=None) -> list[str]:
    """Names of CRIB_JEV_* variables set in the environment. They win over the file (names only)."""
    environ = os.environ if environ is None else environ
    return [k for k in KEYS if (environ.get(k) or "").strip()]


def uses_remote(settings: Settings, live: bool) -> bool:
    """Would a call from this context go to the remote server? Live needs the extra opt-in."""
    if not settings.remote_url_configured:
        return False
    return settings.remote_live if live else True


def status_line(settings: Settings, kind: str = "jev") -> str:
    """One visible line for the GUI: which backend is active (never the URL or token)."""
    if kind != "jev":
        return "판정 백엔드: Qwen 로컬 (원격 설정은 Jev에만 해당)"
    if not settings.remote_url_configured:
        if settings.backend == "remote":
            return "판정 백엔드: 로컬 (원격으로 선택했지만 주소가 없음 → 로컬 사용)"
        return "판정 백엔드: 로컬 (127.0.0.1)"
    host = mask_host(settings.url)
    where = photo_destination(settings.url)
    if settings.remote_live:
        return f"판정 백엔드: 원격 서버 ({host}) — 실시간 감시·사진 테스트 모두 원격. {where}"
    return f"판정 백엔드: 원격 서버 ({host}) — 사진 테스트/점수 측정만. 실시간 감시는 로컬"


def render_file(s: Settings) -> str:
    for v in (s.url, s.token, s.timeout):
        if "\n" in v or "\r" in v:
            raise SettingsError("줄바꿈은 넣을 수 없습니다.")
    lines = [
        "# Local only. Do not commit (gitignored). Holds the server token: keep it private.",
        f"CRIB_JEV_BACKEND={s.backend}",
        f"CRIB_JEV_URL={s.url}",
        f"CRIB_JEV_TOKEN={s.token}",
    ]
    if s.timeout.strip():
        lines.append(f"CRIB_JEV_TIMEOUT={s.timeout.strip()}")
    lines.append(f"CRIB_JEV_REMOTE_LIVE={'1' if s.remote_live else '0'}")
    return "\n".join(lines) + "\n"


def save(s: Settings, path: Path | None = None) -> Path:
    """Write the settings file, 0600 from the start (best effort on Windows). Validates when remote."""
    path = path or env_file_path()
    if s.backend not in ("local", "remote"):
        raise SettingsError("백엔드는 local 또는 remote여야 합니다.")
    if s.backend == "remote":
        # Remote selected: everything must be valid, nothing invalid is written.
        parse_timeout(s.timeout)
        s = validate(s)
    text = render_file(s)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    return path


def korean_error(exc: BaseException) -> str:
    """User-facing Korean text for a connection failure. Built from the exception TYPE and a few
    known fragments only, so nothing from the URL or token can end up in it."""
    msg = str(exc)
    if isinstance(exc, SettingsError):
        return str(exc)
    if "HTTP 401" in msg or "HTTP 403" in msg:
        return "토큰이 거부되었습니다 (401). 토큰을 다시 확인하세요."
    if "HTTP 404" in msg:
        return "서버는 응답했지만 이 경로를 모릅니다 (404). 주소를 확인하세요."
    if "HTTP 5" in msg:
        return "서버나 터널이 응답하지 않습니다 (5xx). 서버가 꺼졌거나 터널/프록시가 끊겼을 수 있습니다."
    if "timeout" in msg:
        return "시간 초과. 서버가 꺼졌거나 느립니다."
    if "tls" in msg:
        return "TLS(https) 연결 오류. 주소와 인증서를 확인하세요."
    if "connection failed" in msg:
        return "연결하지 못했습니다. 주소가 맞는지, 서버(또는 터널)가 살아 있는지 확인하세요."
    return f"연결 테스트 실패 ({type(exc).__name__})"


def test_connection(settings: Settings, timeout: float = 15.0) -> dict:
    """GET /v1/models through the same client code as judging. Returns
    {'ok': bool, 'backend': 'local'|'remote', 'model': str|None, 'ms': int|None, 'message': Korean text}.
    Never raises and never contains the token or the full URL."""
    import jev_protocol as J

    backend = "remote" if settings.remote_url_configured else "local"
    try:
        ep = J.endpoint_from_settings(settings)
        t0 = time.perf_counter()
        model = J.get_models(ep, timeout)
        ms = int((time.perf_counter() - t0) * 1000)
    except SystemExit as e:  # legacy refusals are SystemExit
        return {"ok": False, "backend": backend, "model": None, "ms": None,
                "message": "주소가 허용되지 않습니다: " + str(e)[:120]}
    except Exception as e:
        return {"ok": False, "backend": backend, "model": None, "ms": None, "message": korean_error(e)}
    label = "원격 서버" if backend == "remote" else "로컬"
    name = model or "모델 이름 모름"
    return {"ok": True, "backend": backend, "model": model, "ms": ms,
            "message": f"OK ({label}) 모델: {name}, 응답 {ms} ms"}


def _self_check() -> None:
    import stat
    import tempfile

    # validate_url
    assert validate_url("https://abc-def.trycloudflare.com") == "https://abc-def.trycloudflare.com"
    assert validate_url(" https://Abc.example.com:8443/ ") == "https://abc.example.com:8443"
    assert validate_url("https://abc.example.com/v1/systemone/") == "https://abc.example.com"
    # any path is fine; http only for localhost / private network
    assert validate_url("https://api.example.com/jev/v2/systemone") == "https://api.example.com/jev/v2/systemone"
    assert validate_url("http://127.0.0.1:8090") == "http://127.0.0.1:8090"
    assert validate_url("http://192.168.1.20:9000/x") == "http://192.168.1.20:9000/x"
    assert validate_url("http://[::1]:8090/") == "http://[::1]:8090"
    for bad in ("", "http://abc.trycloudflare.com", "http://8.8.8.8", "http://localhost.evil.com",
                "http://192.168.1.1.evil.com", "http://10.evil.com", "http://user@evil.com@192.168.1.1/",
                "ftp://x.y", "https://", "https://u:p@abc.example.com", "https://abc.example.com/a/../b",
                "https://abc.example.com//x", "https://abc.example.com/a b", "https://abc.example.com/%2e%2e",
                "https://abc.example.com/?x=1", "https://abc.example.com/#f", "https://abc.example.com/a;b",
                "https://abc.example.com:99999", "https://abc.example.com:0", "abc.example.com"):
        try:
            validate_url(bad)
            raise SystemExit(f"accepted url {bad!r}")
        except SettingsError:
            pass
    # token
    assert validate_token(" abcDEF_123-xyz ") == "abcDEF_123-xyz"
    for bad in ("", "   ", "a b", "ab\ncd", "토큰"):
        try:
            validate_token(bad)
            raise SystemExit(f"accepted token {bad!r}")
        except SettingsError:
            pass
    assert validate_token("", required=False) == ""
    try:
        validate_token("", required=True)
        raise SystemExit("empty token accepted")
    except SettingsError:
        pass
    # host scope: IP literals only, names never count as local
    for h, want in (("localhost", "loopback"), ("LOCALHOST", "loopback"), ("127.0.0.1", "loopback"),
                    ("127.9.9.9", "loopback"), ("::1", "loopback"), ("[::1]", "loopback"),
                    ("::ffff:127.0.0.1", "loopback"), ("10.0.0.5", "private"), ("172.16.0.1", "private"),
                    ("172.31.255.255", "private"), ("192.168.1.1", "private"), ("169.254.1.1", "private"),
                    ("fe80::1", "private"), ("fd00::1", "private"), ("::ffff:10.0.0.1", "private"),
                    ("::ffff:8.8.8.8", "public"), ("172.32.0.1", "public"), ("172.15.0.1", "public"),
                    ("100.64.0.1", "public"), ("8.8.8.8", "public"), ("0.0.0.0", "public"),
                    ("localhost.evil.com", "public"), ("localhost.", "public"), ("192.168.1.1.evil.com", "public"),
                    ("10.evil.com", "public"), ("nas.local", "public"), ("127.1", "public"), ("0x7f.1", "public"),
                    ("2130706433", "public"), ("fe80::1%eth0", "public"), ("", "public"), ("example.com", "public")):
        assert host_scope(h) == want, (h, host_scope(h), want)
    # timeout
    assert parse_timeout("") is None and parse_timeout("12.5") == 12.5
    for bad in ("0", "-1", "nan", "abc", "99999"):
        try:
            parse_timeout(bad)
            raise SystemExit(f"accepted timeout {bad}")
        except SettingsError:
            pass
    # mask
    assert mask_host("https://abc-def.trycloudflare.com/x") == "***.trycloudflare.com"
    assert mask_host("https://example.com") == "***.com" and mask_host("") == "***"
    for ip_url in ("http://192.168.10.77:9000/x", "192.168.10.77", "http://[fd00::5]/", "10.1.2.3:8090"):
        m = mask_host(ip_url)
        assert "192" not in m and "77" not in m and "fd00" not in m and "10.1" not in m and "(IP" in m, m
    assert "abc-def" not in mask_host("abc-def.trycloudflare.com:443")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "crib_remote.env"
        secret = "S3cretTokenValue_0123456789"
        # default: nothing set -> local, nothing remote
        s = load({}, path)
        assert s == Settings() and not s.remote_url_configured and not uses_remote(s, False)
        assert status_line(s) == "판정 백엔드: 로컬 (127.0.0.1)"
        # save + load roundtrip, perms
        want = Settings("remote", "https://abc-def.trycloudflare.com", secret, "30", False)
        save(want, path)
        if os.name == "posix":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
        got = load({}, path)
        assert got == want and uses_remote(got, False) and not uses_remote(got, True)
        assert secret not in repr(got) and "abc-def" not in repr(got) and secret not in status_line(got)
        assert "abc-def" not in status_line(got) and "로컬" in status_line(got).split("—")[1]
        live = Settings("remote", want.url, secret, "", True)
        assert uses_remote(live, True) and "집 밖" in status_line(live)
        # file says local: remote keys in the file are ignored (kept for later)
        save(Settings("local", want.url, secret, "", False), path)
        assert load({}, path).url == "" and not load({}, path).remote_url_configured
        # file says remote: env overrides file
        save(want, path)
        over = load({"CRIB_JEV_URL": "https://other.example.com", "CRIB_JEV_TOKEN": "envtoken123"}, path)
        assert over.url == "https://other.example.com" and over.token == "envtoken123" and over.timeout == "30"
        assert load({"CRIB_JEV_BACKEND": "local"}, path).url == ""  # env backend=local ignores the file
        assert load({"CRIB_JEV_REMOTE_LIVE": "1"}, path).remote_live is True
        # env only (no file): https url + token is enough, as documented
        e = load({"CRIB_JEV_URL": "https://x.example.com", "CRIB_JEV_TOKEN": "t0kentoken"}, Path(tmp) / "none")
        assert e.remote_url_configured and e.backend == "local"
        # the plain legacy local form (http loopback /v1/systemone) in the environment is not "remote"
        assert not load({"CRIB_JEV_URL": "http://127.0.0.1:8090/v1/systemone"}, Path(tmp) / "none").remote_url_configured
        # a loopback / private address with its own path is a web API server (remote path), token optional
        assert load({"CRIB_JEV_URL": "http://127.0.0.1:9000/api/systemone"}, Path(tmp) / "none").remote_url_configured
        lan = Settings("remote", "http://192.168.1.20:9000", "", "", True)
        assert validate(lan) == lan and "같은 네트워크" in status_line(lan) and "192" not in status_line(lan)
        save(lan, path)
        assert load({}, path) == lan
        save(want, path)
        # invalid saves are refused and write nothing
        before = path.read_text(encoding="utf-8")
        for bad in (Settings("remote", "http://abc.example.com", secret), Settings("remote", want.url, ""),
                    Settings("remote", "https://abc.example.com/x", ""), Settings("remote", "http://8.8.8.8", secret),
                    Settings("remote", want.url, secret, "abc"), Settings("cloud", "", "")):
            try:
                save(bad, path)
                raise SystemExit("invalid save accepted")
            except SettingsError:
                pass
        assert path.read_text(encoding="utf-8") == before
        try:
            save(Settings("remote", "https://abc.example.com", "tok\nCRIB_JEV_URL=x"), path)
            raise SystemExit("newline token accepted")
        except SettingsError:
            pass
        # file parsing like ntfy.env
        path.write_text("# c\n\nCRIB_JEV_BACKEND = remote\nCRIB_JEV_URL=https://z.example.com\nbad line\n", encoding="utf-8")
        assert parse_env(path)["CRIB_JEV_URL"] == "https://z.example.com"
        assert parse_env(Path(tmp) / "missing") == {}
    f = settings_from_form("remote", " https://a.example.com ", " tok123456 ", "", True)
    assert f == Settings("remote", "https://a.example.com", "tok123456", "", True)
    assert settings_from_form("cloud", "", "").backend == "local"
    assert env_overrides({"CRIB_JEV_URL": "x", "CRIB_JEV_TOKEN": " ", "OTHER": "1"}) == ["CRIB_JEV_URL"]
    # korean_error never echoes the exception text
    assert "토큰" in korean_error(RuntimeError("remote jev HTTP 401 (token rejected)"))
    assert "S3cret" not in korean_error(RuntimeError("weird S3cret"))
    print("ok remote-settings")


if __name__ == "__main__":
    _self_check()
