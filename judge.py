"""Crib model switch. CRIB_MODEL=qwen (default) or jev. Not a medical device."""

from __future__ import annotations

import os


def ask(image, timeout: int = 180) -> dict:
    kind = os.environ.get("CRIB_MODEL", "qwen").strip().lower() or "qwen"
    if kind == "qwen":
        from qwen_client import ask as impl

        return impl(image, timeout=timeout)
    if kind == "jev":
        from jev_protocol import ask as impl

        return impl(image, timeout=timeout)
    raise SystemExit("CRIB_MODEL must be qwen or jev")


if __name__ == "__main__":
    os.environ["CRIB_MODEL"] = "cloud"
    try:
        ask(None)
        raise SystemExit("expected reject")
    except SystemExit as e:
        if "qwen or jev" not in str(e):
            raise
    print("ok judge")
