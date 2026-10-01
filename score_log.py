"""Append-only judgment log: one JSON line per judgment, so scores can be reported later.

Default file: logs/scores.jsonl next to this program. Env:
  CRIB_SCORE_LOG=<path>|off     log file, or off to disable
  CRIB_LOG_SAVE_FRAMES=1        save EVERY judged JPEG to logs/frames/ (baby photos, local only, default OFF)
  CRIB_LOG_SAVE_AMBIGUOUS=lo-hi save only when a rule score is in [lo,hi], e.g. 0.3-0.7 (default OFF)
  CRIB_LOG_MAX_FRAMES=200       keep at most this many saved frames, oldest deleted
Logging never raises. It prints at most one warning per run.
CLI: python score_log.py --tail 20 | --csv | --label <id> <text>     (no args = self-test)
"""

from __future__ import annotations

import csv
import io
import json
import os
import secrets
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

DIR = Path(__file__).resolve().parent
MAX_BYTES = 5 * 1024 * 1024
KEEP = 3  # current file + .1 + .2
DEFAULT_MAX_FRAMES = 200
_RULES = ("face_cover", "face_down", "climbing")
_PARTS = ("face_down_cheek", "face_down_back", "face_down_belly")
_OFF = {"off", "0", "false", "no", "none", "disabled"}
_ON = {"1", "true", "yes", "on"}
_lock = threading.Lock()
_warned = False


def _warn(msg: str) -> None:
    global _warned
    if _warned:
        return
    _warned = True
    try:
        print(f"[score_log] {msg} (further log problems are silent)", file=sys.stderr, flush=True)
    except Exception:
        pass


def log_path() -> Path | None:
    raw = os.environ.get("CRIB_SCORE_LOG", "").strip()
    if raw.lower() in _OFF:
        return None
    return Path(raw) if raw else DIR / "logs" / "scores.jsonl"


def _ambiguous_range() -> tuple[float, float] | None:
    raw = os.environ.get("CRIB_LOG_SAVE_AMBIGUOUS", "").strip()
    if not raw or raw.lower() in _OFF:
        return None
    try:
        lo_s, hi_s = raw.split("-")
        lo, hi = float(lo_s), float(hi_s)
        if not (0.0 <= lo <= hi <= 1.0):  # also rejects nan
            raise ValueError
        return lo, hi
    except ValueError:
        _warn(f"CRIB_LOG_SAVE_AMBIGUOUS={raw!r} must look like 0.3-0.7; frame saving for it is off")
        return None


def _max_frames() -> int:
    raw = os.environ.get("CRIB_LOG_MAX_FRAMES", "").strip()
    if not raw:
        return DEFAULT_MAX_FRAMES
    try:
        n = int(raw)
        if n < 1:
            raise ValueError
        return n
    except ValueError:
        _warn(f"CRIB_LOG_MAX_FRAMES={raw!r} must be an integer >= 1; using {DEFAULT_MAX_FRAMES}")
        return DEFAULT_MAX_FRAMES


def _want_frame(scores: dict) -> bool:
    if os.environ.get("CRIB_LOG_SAVE_FRAMES", "").strip().lower() in _ON:
        return True
    rng = _ambiguous_range()
    if rng is None:
        return False
    return any(rng[0] <= v <= rng[1] for k, v in scores.items() if k in _RULES and isinstance(v, (int, float)))


def _save_frame(image, path: Path, stamp: str, row_id: str) -> str | None:
    if image is None or not hasattr(image, "save"):
        return None
    folder = path.parent / "frames"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{stamp}_{row_id}.jpg"
    image.convert("RGB").save(folder / name, "JPEG", quality=90)
    old = sorted(p for p in folder.glob("*.jpg") if p.is_file())
    for p in old[: max(0, len(old) - _max_frames())]:
        try:
            p.unlink()
        except OSError:
            pass
    return name


def _rotate(path: Path, incoming: int) -> None:
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size == 0 or size + incoming <= MAX_BYTES:
        return
    for i in range(KEEP - 1, 0, -1):
        src = path if i == 1 else path.with_name(f"{path.name}.{i - 1}")
        if src.exists():
            os.replace(src, path.with_name(f"{path.name}.{i}"))


def _append(path: Path, row: dict) -> None:
    line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate(path, len(line.encode("utf-8")))
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)


def _thresholds(model: str) -> dict | None:
    if model != "jev":
        return None
    try:
        from jev_protocol import alert_at

        return {rule: alert_at(rule) for rule in _RULES}
    except BaseException:  # alert_at can SystemExit on a bad env value; the verdict path already handled that
        return None


def log_judgment(result, model: str, source: str, image=None, image_name: str | None = None) -> str | None:
    """Append one row. Returns the row id, or None if disabled or failed. Never raises."""
    try:
        path = log_path()
        if path is None or not isinstance(result, dict):
            return None
        now = datetime.now().astimezone()
        row_id = secrets.token_hex(3)
        scores = result.get("scores") if isinstance(result.get("scores"), dict) else {}
        parts = result.get("face_down_parts") if isinstance(result.get("face_down_parts"), dict) else {}
        frame = None
        try:
            if _want_frame(scores):
                frame = _save_frame(image, path, now.strftime("%Y%m%d-%H%M%S-%f")[:-3], row_id)
        except Exception as exc:
            _warn(f"could not save frame: {type(exc).__name__}")
        _append(path, {
            "type": "score",
            "id": row_id,
            "time": now.isoformat(timespec="seconds"),
            "source": source,
            "model": model,
            "scores": scores,
            "face_down_parts": parts,
            "thresholds": _thresholds(model),
            "alert": bool(result.get("should_alert")),
            "rule": result.get("rule"),
            "reason": str(result.get("reason") or "")[:300],
            "image": image_name,
            "frame": frame,
        })
        return row_id
    except BaseException as exc:  # never break judging because of the log
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        _warn(f"could not write score log: {type(exc).__name__}: {exc}")
        return None


# ---- reading / CLI ----

def _files(path: Path) -> list[Path]:
    names = [path.with_name(f"{path.name}.{i}") for i in range(KEEP - 1, 0, -1)] + [path]
    return [p for p in names if p.is_file()]


def read_rows(path: Path | None = None) -> list[dict]:
    """Score rows oldest first, each with 'label' = latest annotation for its id (or '')."""
    path = path or log_path()
    if path is None:
        return []
    rows, labels = [], {}
    for p in _files(path):
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                if rec.get("type") == "label":
                    labels[rec.get("id")] = str(rec.get("label") or "")
                elif rec.get("id"):
                    rows.append(rec)
    for r in rows:
        r["label"] = labels.get(r["id"], "")
    return rows


def add_label(row_id: str, text: str, path: Path | None = None) -> bool:
    """Append a label record (the file is never rewritten). False if the id is unknown."""
    path = path or log_path()
    if path is None or not any(r["id"] == row_id for r in read_rows(path)):
        return False
    _append(path, {"type": "label", "id": row_id, "label": text,
                   "time": datetime.now().astimezone().isoformat(timespec="seconds")})
    return True


_COLS = ("time", "id", "model", "face_down", "cheek", "back", "belly", "mouth_nose", "alert", "frame", "label")


def _cells(r: dict, full_time: bool) -> list[str]:
    s = r.get("scores") or {}
    p = r.get("face_down_parts") or {}

    def num(d, k):
        v = d.get(k)
        return f"{v:.2f}" if isinstance(v, (int, float)) and not isinstance(v, bool) else "-"

    t = str(r.get("time", ""))
    if not full_time:
        t = t[5:19].replace("T", " ")
    return [t, str(r.get("id", "")), str(r.get("model", "")), num(s, "face_down"),
            num(p, "face_down_cheek"), num(p, "face_down_back"), num(p, "face_down_belly"),
            num(s, "face_cover"), "ALERT" if r.get("alert") else "-",
            "y" if r.get("frame") else "-", str(r.get("label") or "")]


def render(rows: list[dict], as_csv: bool = False) -> str:
    if as_csv:
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        w.writerow(_COLS)
        for r in rows:
            w.writerow(_cells(r, True))
        return out.getvalue()
    table = [list(_COLS)] + [_cells(r, False) for r in rows]
    widths = [max(len(row[i]) for row in table) for i in range(len(_COLS))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in table) + "\n"


def _main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Crib judgment score log")
    ap.add_argument("--tail", type=int, metavar="N", help="show the last N rows")
    ap.add_argument("--csv", action="store_true", help="CSV instead of the aligned table (all rows unless --tail)")
    ap.add_argument("--label", nargs="+", metavar=("ID", "TEXT"), help="annotate row ID, e.g. --label ab12cd real prone")
    a = ap.parse_args(argv)
    path = log_path()
    if path is None:
        print("score log is off (CRIB_SCORE_LOG=off)", file=sys.stderr)
        return 1
    if a.label:
        if len(a.label) < 2:
            ap.error("--label needs an id and a text")
        if not add_label(a.label[0], " ".join(a.label[1:]), path):
            print(f"no row with id {a.label[0]!r} in {path}", file=sys.stderr)
            return 1
        print(f"labeled {a.label[0]}")
        return 0
    rows = read_rows(path)
    if a.tail is not None:
        rows = rows[-a.tail:] if a.tail > 0 else []
    elif not a.csv:
        rows = rows[-20:]
    if not rows and not path.exists():
        print(f"no log yet: {path}", file=sys.stderr)
        return 1
    sys.stdout.write(render(rows, as_csv=a.csv))
    return 0


def _self_check() -> None:
    global MAX_BYTES, _warned
    import contextlib
    import tempfile

    from PIL import Image

    keys = ("CRIB_SCORE_LOG", "CRIB_LOG_SAVE_FRAMES", "CRIB_LOG_SAVE_AMBIGUOUS", "CRIB_LOG_MAX_FRAMES")
    saved_env = {k: os.environ.pop(k, None) for k in keys}
    saved_max = MAX_BYTES
    verdict = {"should_alert": False, "rule": None, "reason": "r",
               "scores": {"face_cover": 0.05, "face_down": 0.35, "climbing": 0.02, "baby_present": 0.9},
               "face_down_parts": {"face_down_cheek": 0.2, "face_down_back": 0.72, "face_down_belly": 0.35}}
    img = Image.new("RGB", (16, 12), (9, 9, 9))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "logs" / "scores.jsonl"
            os.environ["CRIB_SCORE_LOG"] = str(log)
            # write
            rid = log_judgment(verdict, "jev", "gui-test", img, "a.jpg")
            row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
            assert rid == row["id"] and len(rid) == 6 and row["source"] == "gui-test" and row["model"] == "jev"
            assert row["face_down_parts"]["face_down_back"] == 0.72 and row["scores"]["face_down"] == 0.35
            assert row["thresholds"]["face_down"] == 0.60 and row["alert"] is False
            assert row["image"] == "a.jpg" and row["frame"] is None and row["time"][-6] in "+-"
            assert not (log.parent / "frames").exists()  # frames default OFF
            assert verdict["scores"]["face_down"] == 0.35  # result not mutated
            # qwen row has no scores: still logged, '-' in table
            log_judgment({"should_alert": True, "rule": "face_cover", "reason": "x"}, "qwen", "watch")
            rows = read_rows(log)
            assert len(rows) == 2 and rows[1]["thresholds"] is None and rows[1]["alert"] is True
            text = render(rows)
            assert "face_down" in text.splitlines()[0] and "0.35" in text and "ALERT" in text
            assert text.splitlines()[1].split()[4:8] == ["0.35", "0.20", "0.72", "0.35"]
            assert render(rows, as_csv=True).splitlines()[0].startswith("time,id,model,face_down,cheek")
            # labels: append-only, latest wins, unknown id refused
            size = log.stat().st_size
            head = log.read_bytes()
            assert add_label(rid, "real prone", log) and add_label(rid, "held by adult", log)
            assert log.read_bytes().startswith(head) and log.stat().st_size > size
            assert read_rows(log)[0]["label"] == "held by adult" and not add_label("nope00", "x", log)
            assert "held by adult" in render(read_rows(log))
            assert _main(["--label", rid, "back", "lying"]) == 0 and read_rows(log)[0]["label"] == "back lying"
            # frames: ambiguous range, save-all, cap, bad values
            os.environ["CRIB_LOG_SAVE_AMBIGUOUS"] = "0.3-0.7"
            os.environ["CRIB_LOG_MAX_FRAMES"] = "2"
            for _ in range(4):
                log_judgment(verdict, "jev", "watch", img)  # face_down 0.35 is inside
                time.sleep(0.003)
            frames = sorted((log.parent / "frames").glob("*.jpg"))
            assert len(frames) == 2, frames
            last = read_rows(log)[-1]
            assert last["frame"] == frames[-1].name and Image.open(frames[-1]).size == (16, 12)
            n = len(list((log.parent / "frames").glob("*.jpg")))
            quiet = dict(verdict, scores={"face_cover": 0.05, "face_down": 0.9, "climbing": 0.0})
            quiet2 = dict(verdict, scores={"face_cover": 0.05, "face_down": 0.1, "climbing": 0.0})
            log_judgment(quiet, "jev", "watch", img); log_judgment(quiet2, "jev", "watch", img)
            assert len(list((log.parent / "frames").glob("*.jpg"))) == n and read_rows(log)[-1]["frame"] is None
            os.environ["CRIB_LOG_SAVE_FRAMES"] = "1"
            log_judgment(quiet, "jev", "watch", img)
            assert read_rows(log)[-1]["frame"] is not None
            del os.environ["CRIB_LOG_SAVE_FRAMES"]
            for bad in ("abc", "0.7-0.3", "0.3", "-1-2", "0.2-nan"):
                os.environ["CRIB_LOG_SAVE_AMBIGUOUS"] = bad
                _warned = False
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    assert _ambiguous_range() is None and _ambiguous_range() is None
                assert err.getvalue().count("[score_log]") == 1, bad  # one warning only
            os.environ.pop("CRIB_LOG_SAVE_AMBIGUOUS")
            # rotation
            MAX_BYTES = 600
            rot = Path(tmp) / "rot" / "s.jsonl"
            os.environ["CRIB_SCORE_LOG"] = str(rot)
            for _ in range(40):
                log_judgment(verdict, "jev", "watch")
            names = sorted(p.name for p in rot.parent.iterdir())
            assert names == ["s.jsonl", "s.jsonl.1", "s.jsonl.2"], names
            assert all(p.stat().st_size <= 600 + 1 for p in rot.parent.iterdir())
            assert 0 < len(read_rows(rot)) < 40
            MAX_BYTES = saved_max
            # disabled
            off = Path(tmp) / "off" / "s.jsonl"
            for value in ("off", "OFF", "0"):
                os.environ["CRIB_SCORE_LOG"] = value
                assert log_judgment(verdict, "jev", "watch", img) is None and log_path() is None
            assert not (Path(tmp) / "off").exists() and read_rows() == [] and not off.exists()
            # never raises: log dir is a file, image that fails to save, junk result
            blocker = Path(tmp) / "blocker"
            blocker.write_text("x")
            os.environ["CRIB_SCORE_LOG"] = str(blocker / "sub" / "s.jsonl")
            _warned = False
            with contextlib.redirect_stderr(io.StringIO()) as err:
                assert log_judgment(verdict, "jev", "watch", img) is None
                assert log_judgment(verdict, "jev", "watch", img) is None
            assert err.getvalue().count("[score_log]") == 1
            os.environ["CRIB_SCORE_LOG"] = str(log)
            os.environ["CRIB_LOG_SAVE_FRAMES"] = "1"

            class _BadImage:
                def save(self, *a, **k):
                    raise OSError("disk full")

                def convert(self, *a):
                    return self

            _warned = False
            with contextlib.redirect_stderr(io.StringIO()):
                assert log_judgment(verdict, "jev", "watch", _BadImage()) is not None  # row kept, frame null
                assert read_rows(log)[-1]["frame"] is None
                for junk in (None, "x", 3, {"scores": "bad", "face_down_parts": [1]}):
                    log_judgment(junk, "jev", "watch", img)
            # CLI on a log that does not exist yet
            os.environ["CRIB_SCORE_LOG"] = str(Path(tmp) / "none.jsonl")
            with contextlib.redirect_stderr(io.StringIO()):
                assert _main(["--tail", "5"]) == 1
    finally:
        MAX_BYTES = saved_max
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("ok score-log")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] != "--check":
        raise SystemExit(_main(sys.argv[1:]))
    _self_check()
