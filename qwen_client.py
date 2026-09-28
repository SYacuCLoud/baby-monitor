"""Qwen vision JSON. Local llama-server only. Not a medical device."""

from __future__ import annotations

import base64
import http.client
import io
import json
import re

from PIL import Image

QWEN_URL = "http://127.0.0.1:8080/v1/chat/completions"
QWEN_HOST, QWEN_PORT, QWEN_PATH = "127.0.0.1", 8080, "/v1/chat/completions"
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


# Grammar-constrained output: always one object, rule only from ALLOWED (or null).
SCHEMA = {
    "type": "object",
    "properties": {
        "should_alert": {"type": "boolean"},
        "baby_present": {"type": "boolean"},
        "face_visible": {"type": "boolean"},
        "rule": {"enum": [None, *sorted(ALLOWED)]},
        "reason": {"type": "string", "maxLength": 120},
    },
    "required": ["should_alert", "baby_present", "face_visible", "rule", "reason"],
    "additionalProperties": False,
}


def _post(body: dict, timeout: int) -> dict:
    # http.client: loopback only, never follows redirects.
    # ponytail: one retry on a local reset (seen 2/30 against llama-server); the frame never leaves 127.0.0.1.
    data = json.dumps(body).encode("utf-8")
    for attempt in (1, 2):
        conn = http.client.HTTPConnection(QWEN_HOST, QWEN_PORT, timeout=timeout)
        try:
            conn.request("POST", QWEN_PATH, data, {"Content-Type": "application/json"})
            resp = conn.getresponse()
            raw = resp.read()
            if resp.status != 200:
                raise RuntimeError(f"qwen HTTP {resp.status}")
            return json.loads(raw.decode("utf-8"))
        except (ConnectionResetError, http.client.RemoteDisconnected):
            if attempt == 2:
                raise
        finally:
            conn.close()


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
        # Reusing a cached prompt changed temperature-0 answers on the same frame (face_visible true -> false).
        # Each frame is a new image anyway, so the cache saved nothing here.
        "cache_prompt": False,
        "response_format": {"type": "json_schema", "json_schema": {"name": "crib", "schema": SCHEMA}},
    }
    out = _post(body, timeout)
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
    assert set(SCHEMA["properties"]["rule"]["enum"]) == ALLOWED | {None}
    print("ok parse")
