"""Score a folder of photos with the running judge server. READ-ONLY measuring tool.

Usage: python score_folder.py <folder> [--model jev|qwen] [--csv out.csv] [--remote]
       python score_folder.py --selftest

--remote: send the photos to the remote Jev web API server configured in crib_remote.env or CRIB_JEV_URL +
CRIB_JEV_TOKEN (https + token; localhost / private network may use http and no token). Photos leave this PC
(or go to another device on your network). Without --remote the crib_remote.env file is
ignored (unless CRIB_JEV_BACKEND=remote is set in the environment) and nothing changes: local loopback Jev. (An explicit remote CRIB_JEV_URL (+ CRIB_JEV_TOKEN) in the
environment is honored either way.)

Uses the same call as the GUI single-photo test (crib_gui.py _run_test): the photo is opened with
Image.open(path).convert("RGB") and judged with judge.ask under CRIB_MODEL=<model>. No HTTP code here.
It sends NO push, writes NO ticks/status/frames/latest.jpg and NO score log. Only --csv writes a file.
Expected tag comes from the filename prefix (엎드림_ 정상_ 옆으로_ 앉음_ 안김_ 빈침대_ / prone_ normal_ side_ sitting_ held_ empty_).
The threshold range it prints is only a hint. Nothing is applied. Not a medical device.
"""

from __future__ import annotations

import csv
import io
import os
import sys
import unicodedata
from pathlib import Path

EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
MIN_GROUP = 5
# prefix (without the trailing underscore) -> canonical tag. Korean and English forms are the same tag.
PREFIXES = {
    "엎드림": "prone", "prone": "prone",
    "정상": "normal", "normal": "normal",
    "옆으로": "side", "side": "side",
    "앉음": "sitting", "sitting": "sitting",
    "안김": "held", "held": "held",
    "빈침대": "empty", "empty": "empty",
}
TAG_ORDER = ("prone", "normal", "side", "sitting", "held", "empty", "?")
COLS = ("filename", "face_down", "cheek", "back", "belly", "face_cover", "climbing",
        "baby", "adult", "face", "alert", "expected")
HINT_WARNING = "이 범위는 참고용 힌트일 뿐입니다. 자동으로 적용하지 않습니다 (임계값은 그대로)."


def expected_tag(name: str) -> str:
    stem = unicodedata.normalize("NFC", name).lower()  # macOS stores Korean names decomposed
    head, sep, _ = stem.partition("_")
    return PREFIXES.get(head, "?") if sep else "?"


def list_images(folder: Path) -> list[Path]:
    return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXTS),
                  key=lambda p: p.name)


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _row_from(name: str, result: dict) -> dict:
    s = result.get("scores") if isinstance(result.get("scores"), dict) else {}
    p = result.get("face_down_parts") if isinstance(result.get("face_down_parts"), dict) else {}
    return {
        "name": name, "tag": expected_tag(name), "error": None,
        "face_down": _num(s.get("face_down")), "cheek": _num(p.get("face_down_cheek")),
        "back": _num(p.get("face_down_back")), "belly": _num(p.get("face_down_belly")),
        "face_cover": _num(s.get("face_cover")), "climbing": _num(s.get("climbing")),
        # Jev gives scores; Qwen only gives yes/no
        "baby": _num(s.get("baby_present")) if "baby_present" in s else result.get("baby_present"),
        "adult": _num(s.get("adult_present")) if "adult_present" in s else None,
        "face": _num(s.get("face_visible")) if "face_visible" in s else result.get("face_visible"),
        "alert": bool(result.get("should_alert")), "rule": result.get("rule"),
    }


def _error_row(name: str, exc: BaseException) -> dict:
    row = {k: None for k in ("face_down", "cheek", "back", "belly", "face_cover", "climbing",
                             "baby", "adult", "face", "rule")}
    row.update(name=name, tag=expected_tag(name), alert=None,
               error=f"{type(exc).__name__}: {exc}"[:200])
    return row


def default_ask(model: str):
    """The GUI test's call: judge.ask(image) with CRIB_MODEL set. Env is restored by the caller."""
    def run(image):
        from judge import ask

        return ask(image)
    return run


def score_folder(folder: Path, ask_fn, on_row=None) -> list[dict]:
    from PIL import Image

    rows = []
    for path in list_images(folder):
        try:
            image = Image.open(path).convert("RGB")  # same as the GUI file test
            row = _row_from(path.name, ask_fn(image))
        except Exception as exc:  # one bad photo or one failed call must not stop the batch
            row = _error_row(path.name, exc)
        rows.append(row)
        if on_row:
            on_row(row)
    return rows


def _cell(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    return f"{v:.2f}"


def _cells(r: dict) -> list[str]:
    alert = "ERR" if r["error"] else _cell(r["alert"])
    return [r["name"]] + [_cell(r[k]) for k in ("face_down", "cheek", "back", "belly", "face_cover",
            "climbing", "baby", "adult", "face")] + [alert, r["tag"]]


def _width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def render_table(rows: list[dict]) -> str:
    table = [list(COLS)] + [_cells(r) for r in rows]
    widths = [max(_width(row[i]) for row in table) for i in range(len(COLS))]
    lines = ["  ".join(c + " " * (w - _width(c)) for c, w in zip(row, widths)).rstrip() for row in table]
    return "\n".join(lines) + "\n"


def render_csv(rows: list[dict]) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(COLS + ("rule", "error"))
    for r in rows:
        w.writerow(_cells(r) + [r["rule"] or "", r["error"] or ""])
    return out.getvalue()


def _stats(vals: list[float]) -> str:
    return f"{min(vals):.2f}/{sum(vals) / len(vals):.2f}/{max(vals):.2f}" if vals else "-"


def suggest(rows: list[dict]) -> tuple[str, tuple[float, float] | None]:
    prone = [r["face_down"] for r in rows if r["tag"] == "prone" and r["face_down"] is not None]
    other = [r["face_down"] for r in rows if r["tag"] not in ("prone", "?") and r["face_down"] is not None]
    if len(prone) < MIN_GROUP or len(other) < MIN_GROUP:
        return f"not enough photos (need >={MIN_GROUP} per group)", None
    lo, hi = max(other), min(prone)
    if lo >= hi:  # no threshold t can have every non-prone < t <= every prone
        return f"not separable (non-prone max {lo:.2f} >= prone min {hi:.2f})", None
    return f"face_down threshold between {lo:.2f} (exclusive) and {hi:.2f} (inclusive)", (lo, hi)


def render_summary(rows: list[dict], current: float | None = None) -> str:
    lines = ["", "summary per expected tag (min/mean/max)",
             f"{'tag':<8}{'n':>3}  {'face_down':<17}{'face_cover':<17}"]
    for tag in TAG_ORDER:
        sel = [r for r in rows if r["tag"] == tag and not r["error"]]
        if not sel:
            continue
        fd = [r["face_down"] for r in sel if r["face_down"] is not None]
        fc = [r["face_cover"] for r in sel if r["face_cover"] is not None]
        lines.append(f"{tag:<8}{len(sel):>3}  {_stats(fd):<17}{_stats(fc):<17}".rstrip())
    msg, _ = suggest(rows)
    lines.append("")
    if current is not None:
        lines.append(f"current face_down alert threshold: {current:.2f}")
    lines.append(f"suggested {msg}")
    lines.append(HINT_WARNING)
    return "\n".join(lines) + "\n"


def run(folder: Path, model: str, csv_path: Path | None = None, ask_fn=None, out=None, remote: bool = False) -> int:
    out = out or sys.stdout
    if not folder.is_dir():
        print(f"not a folder: {folder}", file=sys.stderr)
        return 2
    if not list_images(folder):
        print(f"no photos ({', '.join(sorted(EXTS))}) in {folder}", file=sys.stderr)
        return 2
    current = None
    prev = os.environ.get("CRIB_MODEL")
    prev_backend = os.environ.get("CRIB_JEV_BACKEND")
    os.environ["CRIB_MODEL"] = model  # same as the GUI test
    if model == "jev":
        # --remote: crib_remote.env counts. Default: the file is ignored (env CRIB_JEV_BACKEND wins only if given).
        if remote:
            os.environ["CRIB_JEV_BACKEND"] = "remote"
        else:
            os.environ.setdefault("CRIB_JEV_BACKEND", "local")
    try:
        if model == "jev":
            from jev_protocol import resolve_endpoint

            try:
                ep = resolve_endpoint(live=False)
            except SystemExit:
                raise
            except Exception as exc:  # RemoteJevError: settings invalid (no URL/token in the text)
                print(f"remote settings: {exc}", file=sys.stderr)
                return 2
            if remote and ep.kind != "remote":
                print("--remote needs a remote CRIB_JEV_URL (https + CRIB_JEV_TOKEN, or http on localhost/private network) in env or crib_remote.env.", file=sys.stderr)
                return 2
            if ep.kind == "remote":
                from remote_settings import destination_for_scope, host_scope, mask_host

                out.write(f"backend: remote ({mask_host(ep.host)}) - {destination_for_scope(host_scope(ep.host))}\n")
            from jev_protocol import alert_at

            current = alert_at("face_down")  # SystemExit with a clear message on a bad env value
        rows = score_folder(folder, ask_fn or default_ask(model))
    finally:
        if prev is None:
            os.environ.pop("CRIB_MODEL", None)
        else:
            os.environ["CRIB_MODEL"] = prev
        if prev_backend is None:
            os.environ.pop("CRIB_JEV_BACKEND", None)
        else:
            os.environ["CRIB_JEV_BACKEND"] = prev_backend
    out.write(render_table(rows))
    errors = [r for r in rows if r["error"]]
    for r in errors:
        out.write(f"error  {r['name']}: {r['error']}\n")
    out.write(render_summary(rows, current))
    if csv_path is not None:
        try:
            with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:  # BOM so Excel reads Korean
                fh.write(render_csv(rows))
            out.write(f"csv: {csv_path}\n")
        except OSError as exc:
            print(f"could not write csv: {exc}", file=sys.stderr)
            return 1
    if len(errors) == len(rows):
        print("every photo failed. Is the judge server running (python imajev_serve.py)?", file=sys.stderr)
        return 1
    return 0


def _selftest() -> None:
    import contextlib
    import tempfile

    from PIL import Image

    names = list(sys.modules)
    saved = {k: os.environ.pop(k, None) for k in list(os.environ) if k.startswith("CRIB_JEV_ALERT_AT")}
    saved["CRIB_MODEL"] = os.environ.pop("CRIB_MODEL", None)
    here = Path(__file__).resolve().parent
    watched = [here / "frames", here / "logs"]

    def snap():
        return [sorted(str(p) for p in d.rglob("*")) if d.exists() else None for d in watched]

    before = snap()
    try:
        assert expected_tag("엎드림_a.jpg") == "prone" and expected_tag("Prone_1.JPG") == "prone"
        assert expected_tag(unicodedata.normalize("NFD", "빈침대_1.jpg")) == "empty"
        assert expected_tag("x_1.jpg") == "?" and expected_tag("prone.jpg") == "?" and expected_tag("normal1.jpg") == "?"
        from jev_protocol import verdict_from_answers

        def stub(image):  # red channel = face_down, green = face_cover; blue 255 = server failure
            r, g, b = image.getpixel((0, 0))
            if b > 200:
                raise RuntimeError("server said no")
            v, c = r / 255, g / 255
            noul = lambda x: {"type": "noul", "noul": x}  # noqa: E731
            return verdict_from_answers({
                "baby_present": noul(0.9), "adult_present": noul(0.1), "face_visible": noul(0.8),
                "face_cover": noul(c), "face_down_cheek": noul(v), "face_down_back": noul(v),
                "face_down_belly": noul(0.0), "climbing": noul(0.0)})

        def put(folder, name, r, g=0, b=0):
            Image.new("RGB", (8, 8), (r, g, b)).save(folder / name, "JPEG", quality=100)

        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()):
            d = Path(tmp) / "photos"
            d.mkdir()
            for i, v in enumerate((180, 200, 220, 230, 245)):
                put(d, f"엎드림_{i}.jpg", v)
            for i, v in enumerate((10, 40, 70, 90, 100)):
                put(d, f"normal_{i}.jpg", v, 20)
            put(d, "정상_cover.jpg", 10, 240)
            put(d, "mystery.jpg", 120)
            put(d, "앉음_err.jpg", 0, 0, 255)  # stub raises
            (d / "정상_broken.jpg").write_bytes(b"not a jpeg")
            (d / "notes.txt").write_text("ignored")
            out_csv = Path(tmp) / "out.csv"
            buf = io.StringIO()
            code = run(d, "jev", out_csv, ask_fn=stub, out=buf)
            text = buf.getvalue()
            assert code == 0, text
            lines = text.splitlines()
            assert lines[0].split() == list(COLS), lines[0]
            body = lines[1:15]
            names_in_table = [ln.split()[0] for ln in body]
            assert names_in_table == sorted(names_in_table) and len(body) == 14 and "notes.txt" not in text
            by = {ln.split()[0]: ln.split() for ln in body}
            # aligned: every row's columns start at the same place (CJK counted double)
            starts = {_width(ln[: ln.index("  ")]) for ln in body[:1]}
            assert len(starts) == 1
            def px(name, ch=0):  # JPEG changes pixels by +-1, so read back what the stub really saw
                return Image.open(d / name).convert("RGB").getpixel((0, 0))[ch] / 255

            f2 = lambda x: f"{x:.2f}"  # noqa: E731
            assert by["엎드림_4.jpg"][1] == f2(px("엎드림_4.jpg")) == "0.96"
            assert by["엎드림_4.jpg"][10] == "yes" and by["엎드림_4.jpg"][11] == "prone"
            assert by["엎드림_0.jpg"][1] == f2(px("엎드림_0.jpg")) and by["엎드림_0.jpg"][10] == "yes"  # >= 0.60
            assert by["normal_4.jpg"][1] == f2(px("normal_4.jpg")) and by["normal_4.jpg"][10] == "no"
            v0 = f2(px("normal_0.jpg"))
            assert by["normal_0.jpg"][2:5] == [v0, v0, "0.00"]  # cheek back belly
            assert by["정상_cover.jpg"][5] == f2(px("정상_cover.jpg", 1)) and by["정상_cover.jpg"][10] == "yes"  # face_cover rule
            assert by["mystery.jpg"][11] == "?" and by["앉음_err.jpg"][10] == "ERR" and by["정상_broken.jpg"][10] == "ERR"
            assert by["엎드림_0.jpg"][7:10] == ["0.90", "0.10", "0.80"]  # baby adult face
            assert "error  앉음_err.jpg: RuntimeError: server said no" in text
            assert "error  정상_broken.jpg:" in text
            assert "current face_down alert threshold: 0.60" in text
            summary = text.split("summary per expected tag")[1]
            pv = [px(f"엎드림_{i}.jpg") for i in range(5)]
            assert f"{min(pv):.2f}/{sum(pv) / 5:.2f}/{max(pv):.2f}" in summary, summary
            nmax = max(px(f"normal_{i}.jpg") for i in range(5))  # 정상_cover (face_down ~0.04) is lower
            assert f"suggested face_down threshold between {nmax:.2f} (exclusive) and {min(pv):.2f} (inclusive)" in text
            assert "참고용 힌트" in text
            sline = [ln for ln in summary.splitlines() if ln.startswith("normal")][0].split()
            assert sline[1] == "6", sline  # 5 normal_ + 정상_cover (broken one is an error, not counted)
            rows_csv = list(csv.reader(io.StringIO(out_csv.read_text(encoding="utf-8-sig"))))
            assert rows_csv[0][:2] == ["filename", "face_down"] and rows_csv[0][-2:] == ["rule", "error"]
            assert len(rows_csv) == 15 and any("server said no" in r[-1] for r in rows_csv)
            assert out_csv.read_bytes().startswith(b"\xef\xbb\xbf")
            # the >=5 rule
            small = Path(tmp) / "small"
            small.mkdir()
            for i, v in enumerate((200, 210, 220, 230)):
                put(small, f"prone_{i}.jpg", v)
            for i in range(6):
                put(small, f"normal_{i}.jpg", 10 + i)
            buf = io.StringIO()
            assert run(small, "jev", None, ask_fn=stub, out=buf) == 0
            assert "not enough photos (need >=5 per group)" in buf.getvalue() and "inclusive" not in buf.getvalue()
            put(small, "prone_4.jpg", 240)
            assert "not enough photos" not in (lambda b: (run(small, "jev", None, ask_fn=stub, out=b), b.getvalue())[1])(io.StringIO())
            # overlap -> not separable; unit-level
            ov = [{"tag": "prone", "face_down": v} for v in (0.5, 0.6, 0.7, 0.8, 0.9)] + \
                 [{"tag": "normal", "face_down": v} for v in (0.1, 0.2, 0.3, 0.4, 0.65)]
            assert suggest(ov)[0].startswith("not separable") and suggest(ov)[1] is None
            eq = [{"tag": "prone", "face_down": 0.6}] * 5 + [{"tag": "held", "face_down": 0.6}] * 5
            assert suggest(eq)[1] is None  # equal is not separable either
            # non-prone group counts all non-prone tags, unknown tags count for neither
            mix = [{"tag": "prone", "face_down": 0.9}] * 5 + [{"tag": "side", "face_down": 0.2}] * 3 + \
                  [{"tag": "held", "face_down": 0.3}] * 2 + [{"tag": "?", "face_down": 0.95}] * 4
            assert suggest(mix)[1] == (0.3, 0.9)
            # empty / missing folder / all failing
            assert run(Path(tmp) / "nope", "jev", None, out=io.StringIO()) == 2
            empty = Path(tmp) / "empty"
            empty.mkdir()
            assert run(empty, "jev", None, out=io.StringIO()) == 2
            # Qwen style result (no scores): rendered with '-' and yes/no, never crashes
            q = lambda image: {"should_alert": True, "baby_present": True, "face_visible": False, "rule": "face_down"}  # noqa: E731
            buf = io.StringIO()
            assert run(small, "qwen", None, ask_fn=q, out=buf) == 0
            assert "face_down" in buf.getvalue() and "not enough photos" in buf.getvalue()
            assert os.environ.get("CRIB_MODEL") is None  # restored
            # a bad CRIB_MODEL through the real path: judge.ask rejects, rows are errors, exit 1
            real = io.StringIO()
            try:
                run(small, "cloud", None, out=real)
            except SystemExit:
                pass
        # read-only: nothing from the app's write paths was imported or created
        new = set(sys.modules) - set(names)
        assert not ({"score_log", "watch", "ntfy_alert", "crib_gui"} & new), new
        assert snap() == before
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("ok score-folder")


def _main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Score a folder of photos (read-only; no push, no logs)")
    ap.add_argument("folder", nargs="?", help="folder with photos, named like 엎드림_01.jpg or prone_01.jpg")
    ap.add_argument("--model", choices=("jev", "qwen"), default="jev")
    ap.add_argument("--csv", metavar="OUT.CSV", help="also write the table as CSV")
    ap.add_argument("--remote", action="store_true",
                    help="use the remote Jev server from crib_remote.env / env. Photos leave this PC")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        _selftest()
        return 0
    if not a.folder:
        ap.error("folder is required (or --selftest)")
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    return run(Path(a.folder), a.model, Path(a.csv) if a.csv else None, remote=a.remote)


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
