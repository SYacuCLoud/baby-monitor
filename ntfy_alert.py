"""Text-only ntfy push. No images. Not a medical device. Not a nanny."""

from __future__ import annotations

import json
import os
import secrets
import urllib.request
from pathlib import Path

DIR = Path(__file__).resolve().parent
ENV_PATH = DIR / "ntfy.env"
SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
TITLE = "아기"

# JSON rule -> lock-screen line. Keep short.
RULE_LINE = {
    "face_cover": "입코 가림",
    "face_down": "얼굴 파묻힘",
    "climbing": "난간 기어오름",
    "empty": "아기 없음",
}


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


def ensure_topic() -> str:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if topic:
        return topic
    data = _parse_env(ENV_PATH)
    topic = data.get("NTFY_TOPIC", "").strip()
    if topic:
        return topic
    topic = "crib-" + secrets.token_urlsafe(24)
    ENV_PATH.write_text(
        "# Local ntfy topic. Text alerts only. Do not attach photos.\n"
        f"NTFY_TOPIC={topic}\n",
        encoding="utf-8",
    )
    return topic


def format_alert(rule: str | None, reason: str = "") -> tuple[str, str]:
    """Return (title, body) in Korean. No photos."""
    head = RULE_LINE.get(rule or "", "확인 필요")
    extra = (reason or "").strip()
    if extra and extra not in head:
        body = f"{head}. {extra}. Tapo 확인"
    else:
        body = f"{head}. Tapo 확인"
    return TITLE, body


def notify_alert(rule: str | None, reason: str = "") -> None:
    title, body = format_alert(rule, reason)
    notify(body, title=title)


def notify(message: str, title: str = TITLE, priority: int = 5) -> None:
    text = (message or "").strip()
    if not text:
        raise ValueError("empty message")
    if "\x00" in text:
        raise ValueError("binary payload blocked")
    topic = ensure_topic()
    payload = json.dumps(
        {"topic": topic, "title": title, "message": text, "priority": priority},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        SERVER,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


if __name__ == "__main__":
    import sys

    t = ensure_topic()
    assert t and all(c.isalnum() or c in "-_" for c in t)
    try:
        notify("")
        raise SystemExit("empty message should fail")
    except ValueError:
        pass
    title, body = format_alert("face_cover", "이불이 코 쪽에 있다")
    assert title == "아기" and body.startswith("입코 가림")
    print("ok topic_file", ENV_PATH)
    print("subscribe in ntfy app:", t[:6] + "..." + " (full topic is in ntfy.env)")
    if "--ping" in sys.argv:
        notify("테스트. Tapo 확인", title=TITLE)
        print("ping sent")
