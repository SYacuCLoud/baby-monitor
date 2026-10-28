"""Crib model switch. CRIB_MODEL=jev (default), qwen, or lightning. Not a medical device."""

from __future__ import annotations

import os


def model_kind() -> str:
    return os.environ.get("CRIB_MODEL", "jev").strip().lower() or "jev"


def ask(image, timeout: int = 180) -> dict:
    kind = model_kind()
    if kind == "qwen":
        from qwen_client import ask as impl

        return impl(image, timeout=timeout)
    if kind == "jev":
        from jev_protocol import ask as impl

        return impl(image, timeout=timeout)
    if kind == "lightning":
        # Same /v1/systemone contract as Jev; Lightning shim listens on 8741.
        os.environ.setdefault("CRIB_JEV_URL", "http://127.0.0.1:8741/v1/systemone")
        from jev_protocol import ask as impl

        return impl(image, timeout=timeout)
    raise SystemExit("CRIB_MODEL must be qwen, jev, or lightning")


if __name__ == "__main__":
    prev = os.environ.pop("CRIB_MODEL", None)
    assert model_kind() == "jev"
    if prev is not None:
        os.environ["CRIB_MODEL"] = prev
    os.environ["CRIB_MODEL"] = "cloud"
    try:
        ask(None)
        raise SystemExit("expected reject")
    except SystemExit as e:
        if "qwen, jev, or lightning" not in str(e):
            raise
    print("ok judge")
