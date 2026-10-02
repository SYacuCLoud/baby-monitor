"""Remote Jev (Google Colab) settings: load / save / validate / mask. Pure stdlib, no tkinter.

Remote mode is OPT IN. Nothing here changes the default (local loopback Jev only).

Settings come from environment variables and the optional file crib_remote.env (gitignored, 0600
best effort). Env overrides the file. The file only counts when its CRIB_JEV_BACKEND is 'remote'
(or env CRIB_JEV_BACKEND=remote); env CRIB_JEV_BACKEND=local ignores the file.

  CRIB_JEV_BACKEND      local (default) | remote
  CRIB_JEV_URL          https://xxxx.trycloudflare.com   (https only, no credentials)
  CRIB_JEV_TOKEN        bearer token printed by the Colab notebook
  CRIB_JEV_TIMEOUT      seconds per request (remote only), default = caller's timeout (180)
  CRIB_JEV_REMOTE_LIVE  1 = live watch may use remote too. Default 0: live watch stays local.

The token and the full URL are never put into error text or logs. Use mask_host().
Self-test: python remote_settings.py
"""

from __future__ import annotations

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
    host = (urlparse(raw).hostname if "://" in raw else raw.split("/")[0].split(":")[0]) or ""
    labels = [p for p in host.lower().split(".") if p]
    if len(labels) >= 3:
        return "***." + ".".join(labels[-2:])
    if len(labels) == 2:
        return "***." + labels[-1]
    return "***"


def is_loopback_host(host: str | None) -> bool:
    return (host or "").lower() in LOOPBACK


def is_remote_url(url: str) -> bool:
    """True for anything that is not an http loopback URL (those stay on the legacy local path)."""
    p = urlparse((url or "").strip())
    return not (p.scheme == "http" and is_loopback_host(p.hostname))


def validate_url(url: str) -> str:
    """Return the normalized base 'https://host[:port]'. Raise SettingsError otherwise."""
    raw = (url or "").strip()
    if not raw:
        raise SettingsError("원격 주소가 비어 있습니다.")
    try:
        p = urlparse(raw)
        port = p.port
    except ValueError:
        raise SettingsError("원격 주소 형식이 올바르지 않습니다.") from None
    if p.scheme != "https":
        raise SettingsError("원격 주소는 https만 허용합니다 (http는 거부).")
    if not p.hostname:
        raise SettingsError("원격 주소에 호스트가 없습니다.")
    if p.username or p.password:
        raise SettingsError("원격 주소에 아이디/비밀번호를 넣지 마세요. 토큰은 따로 입력합니다.")
    if p.query or p.fragment or p.params:
        raise SettingsError("원격 주소에 ?, #, ; 부분을 넣지 마세요.")
    if p.path.rstrip("/") not in ("", "/v1/systemone"):
        raise SettingsError("원격 주소는 https://호스트 형태의 기본 주소여야 합니다 (경로 없음).")
    host = p.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    return f"https://{host}" + (f":{port}" if port else "")


def validate_token(token: str) -> str:
    tok = (token or "").strip()
    if not tok:
        raise SettingsError("토큰이 비어 있습니다. 원격은 토큰이 있어야 합니다.")
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
    return Settings(s.backend, validate_url(s.url), validate_token(s.token), s.timeout.strip(), s.remote_live)


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
    if settings.remote_live:
        return f"판정 백엔드: 원격 ({host}) — 실시간 감시·사진 테스트 모두 원격. 사진이 집 밖으로 나갑니다"
    return f"판정 백엔드: 원격 ({host}) — 사진 테스트/점수 측정만. 실시간 감시는 로컬"


def render_file(s: Settings) -> str:
    for v in (s.url, s.token, s.timeout):
        if "\n" in v or "\r" in v:
            raise SettingsError("줄바꿈은 넣을 수 없습니다.")
    lines = [
        "# Local only. Do not commit (gitignored). Holds the Colab token: keep it private.",
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
        s = Settings("remote", validate_url(s.url), validate_token(s.token), s.timeout, s.remote_live)
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
        return "서버나 터널이 응답하지 않습니다 (5xx). Colab이 꺼졌거나 터널이 끊겼을 수 있습니다."
    if "timeout" in msg:
        return "시간 초과. Colab이 꺼졌거나 느립니다."
    if "tls" in msg:
        return "TLS(https) 연결 오류. 주소를 확인하세요."
    if "connection failed" in msg:
        return "연결하지 못했습니다. 주소가 맞는지, Colab 터널이 살아 있는지 확인하세요."
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
    label = "원격" if backend == "remote" else "로컬"
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
    for bad in ("", "http://abc.trycloudflare.com", "http://127.0.0.1:8090", "ftp://x.y", "https://",
                "https://u:p@abc.example.com", "https://abc.example.com/other",
                "https://abc.example.com/?x=1", "https://abc.example.com:99999", "abc.example.com"):
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
        # a local loopback CRIB_JEV_URL in the environment is not "remote"
        assert not load({"CRIB_JEV_URL": "http://127.0.0.1:8090/v1/systemone"}, Path(tmp) / "none").remote_url_configured
        # invalid saves are refused and write nothing
        before = path.read_text(encoding="utf-8")
        for bad in (Settings("remote", "http://abc.example.com", secret), Settings("remote", want.url, ""),
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
