"""Qwen vision JSON. Local llama-server only. Not a medical device."""

from __future__ import annotations

import base64
import io
import json
import re
import urllib.request

from PIL import Image

QWEN_URL = "http://127.0.0.1:8080/v1/chat/completions"
PROMPT_PATH = __import__("pathlib").Path(__file__).resolve().parent / "prompt.txt"


MAX_EDGE = 1280


def _shrink(image: Image.Image) -> Image.Image:
    rgb = image.convert("RGB")
    w, h = rgb.size
    m = max(w, h)
    if m <= MAX_EDGE:
        return rgb
    s = MAX_EDGE / m
    return rgb.resize((int(w * s), int(h * s)), Image.Resampling.BILINEAR)


def _jpeg_b64(image: Image.Image) -> str:
    buf = io.BytesIO()
    _shrink(image).save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


ALLOWED = frozenset({"face_cover", "face_down", "climbing", "empty"})
NEED_PERSON = frozenset({"face_cover", "face_down", "climbing"})
COPY_RE = re.compile(
    r"(입이나 코|얼굴을 가리|매트리스에 파묻|난간을 타고|기어오름|이불/베개)"
)
TRUE_SET = frozenset({"true", "1", "yes"})
FALSE_SET = frozenset({"false", "0", "no", "null", ""})


def as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    s = str(value).strip().lower()
    if s in TRUE_SET:
        return True
    if s in FALSE_SET:
        return False
    return default


def parse_json_obj(text: str) -> dict:
    raw = (text or "").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("no json")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("json not object")
    return data


def normalize(data: dict) -> dict:
    alert = as_bool(data.get("should_alert"), False)
    present = as_bool(data.get("baby_present"), False) or as_bool(
        data.get("person_present"), False
    )
    if "face_visible" not in data or data.get("face_visible") is None:
        face_vis = None
    else:
        face_vis = as_bool(data.get("face_visible"), False)
    rule = data.get("rule")
    if rule in (None, "", "null"):
        rule = None
    else:
        rule = str(rule)
        if rule not in ALLOWED:
            rule = None
            alert = False
    reason = str(data.get("reason") or "").strip()
    if COPY_RE.search(reason):
        reason = ""
    if not alert:
        rule = None
    if alert and rule is None:
        alert = False
    if alert and rule in NEED_PERSON and not present:
        alert = False
        rule = None
    if alert and rule == "empty" and present:
        alert = False
        rule = None
    return {
        "should_alert": alert,
        "baby_present": present,
        "face_visible": face_vis,
        "rule": rule,
        "reason": reason,
    }


def ask(image: Image.Image, timeout: int = 180) -> dict:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + _jpeg_b64(image)},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": 256,
    }
    req = urllib.request.Request(
        QWEN_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    content = out["choices"][0]["message"]["content"]
    return normalize(parse_json_obj(content))


if __name__ == "__main__":
    sample = '{"should_alert":true,"baby_present":true,"rule":"face_cover","reason":"x"}'
    got = parse_json_obj("blah " + sample + " tail")
    assert got["rule"] == "face_cover"
    bad = normalize(
        {
            "should_alert": False,
            "baby_present": False,
            "rule": "face_cover",
            "reason": "이불이 얼굴을 가리고 있다",
        }
    )
    assert bad == {
        "should_alert": False,
        "baby_present": False,
        "face_visible": None,
        "rule": None,
        "reason": "",
    }
    hit = normalize(
        {
            "should_alert": False,
            "baby_present": True,
            "face_visible": False,
            "rule": None,
            "reason": "x",
        }
    )
    assert hit["should_alert"] is False and hit["rule"] is None
    assert as_bool("false") is False
    assert as_bool("true") is True
    assert normalize({"should_alert": "false", "baby_present": "true", "rule": None})[
        "should_alert"
    ] is False
    print("ok parse")
