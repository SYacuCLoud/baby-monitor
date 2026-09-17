"""Crib motion gate: pixel absdiff. Not a medical device. Not a nanny."""

from __future__ import annotations

import numpy as np
from PIL import Image

# ponytail: one global threshold. Raise PIXEL_DELTA if IR noise wakes every frame.
PIXEL_DELTA = 25
MIN_CHANGED = 0.02


def to_gray(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"), dtype=np.int16)


def changed_fraction(
    prev: np.ndarray,
    curr: np.ndarray,
    pixel_delta: int = PIXEL_DELTA,
) -> float:
    if prev.shape != curr.shape:
        curr = np.asarray(
            Image.fromarray(np.clip(curr, 0, 255).astype(np.uint8)).resize(
                (prev.shape[1], prev.shape[0])
            ),
            dtype=np.int16,
        )
    return float(np.mean(np.abs(curr - prev) > pixel_delta))


def should_wake(frac: float, min_changed: float = MIN_CHANGED) -> bool:
    return frac >= min_changed


if __name__ == "__main__":
    still = Image.new("RGB", (64, 48), (20, 20, 20))
    moved = still.copy()
    moved.paste((200, 200, 200), (8, 8, 24, 24))
    a = to_gray(still)
    b = to_gray(still)
    c = to_gray(moved)
    quiet = changed_fraction(a, b)
    busy = changed_fraction(a, c)
    assert not should_wake(quiet), quiet
    assert should_wake(busy), busy
    print(f"ok quiet={quiet:.4f} busy={busy:.4f}")
