"""Small local window for the crib watch. Not a medical device.

Starts watch.py and the selected model server. The photo stays in this window.
ntfy still gets text only, and only if 푸시 is on. Secrets in rtsp.env and
ntfy.env are not shown or edited.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from PIL import Image, ImageTk

from judge import model_kind
from jev_protocol import ALERT_AT
from score_log import log_judgment

DIR = Path(__file__).resolve().parent
FRAME = DIR / "frames" / "latest.jpg"
STATUS = DIR / "frames" / "status.json"
LOG = DIR / "frames" / "ticks.jsonl"
PORTS = {"jev": 8090, "qwen": 8080}
HISTORY = 50
PREVIEW = 720

IMAJEV_PY = Path(os.environ.get("IMAJEV_PY", r"D:\Dev\imajev\.venv\Scripts\python.exe"))
LLAMA = Path(os.environ.get("LLAMA_SERVER", r"D:\Dev\llama.cpp\llama-server.exe"))
QWEN_DIR = Path(os.environ.get("QWEN_DIR", r"D:\Dev\_Models\Qwen3-VL-4B"))

_NO_WINDOW = 0x08000000


def tail_ticks(path: Path, n: int = HISTORY) -> list[dict]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    rows = []
    for line in lines[-n:]:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    rows.reverse()
    return rows


def format_tick(row: dict) -> str:
    rule = row.get("rule") or "-"
    alert = "알림" if row.get("should_alert") else "조용"
    return f"{row.get('ts', '')}  {alert}  {rule}  {row.get('action', '')}"


RULE_KO = {
    "face_cover": "입코 가림",
    "face_down": "엎드림",
    "climbing": "난간",
    "empty": "아무도 없음",
}


def verdict_text(row: dict | None) -> tuple[str, str]:
    """Big line, then the one sentence under it. Korean, not True/False."""
    if not row:
        return "판정 없음", ""
    if row.get("error"):
        return "판정 실패", str(row.get("reason") or "")
    if row.get("skipped"):
        return "움직임 없음", "모델을 부르지 않았습니다."
    rule = RULE_KO.get(row.get("rule") or "", "")
    head = f"알림: {rule}" if row.get("should_alert") else "알림 없음"
    bits = []
    if row.get("baby_present") is True:
        bits.append("아기 있음")
    elif row.get("baby_present") is False:
        bits.append("아기 없음")
    if row.get("face_visible") is True:
        bits.append("얼굴 보임")
    elif row.get("face_visible") is False:
        bits.append("얼굴 안 보임")
    reason = str(row.get("reason") or "").strip()
    if reason:
        bits.append(reason)
    scores = row.get("scores") if isinstance(row.get("scores"), dict) else None
    nums = []
    if scores:
        order = ("face_cover", "face_down", "climbing", "baby_present", "adult_present", "face_visible")
        names = {"baby_present": "아기", "adult_present": "어른", "face_visible": "얼굴", **RULE_KO}
        nums = [f"{names.get(key, key)} {float(scores[key]):.2f}" for key in order if key in scores]
        parts = row.get("face_down_parts") if isinstance(row.get("face_down_parts"), dict) else None
        if parts and "face_down" in scores:
            cues = (("face_down_cheek", "볼"), ("face_down_back", "등"), ("face_down_belly", "배"))
            try:
                text = " / ".join(f"{ko} {float(parts[key]):.2f}" for key, ko in cues if key in parts)
            except (TypeError, ValueError):
                text = ""
            if text:  # absent for qwen or old results
                i = next(i for i, n in enumerate(nums) if n.startswith(RULE_KO["face_down"]))
                nums[i] += f"  ({text})"
    detail = ". ".join(bits)
    if nums:
        detail = (detail + "\n" if detail else "") + "\n".join(nums) + f"\n알림 기준 {ALERT_AT:.2f}"
    return head, detail


def format_test(row: dict) -> str:
    head, detail = verdict_text(row)
    return head if not detail else f"{head}\n{detail}"


def image_from_clipboard(value) -> tuple[Image.Image | None, str | None]:
    if isinstance(value, Image.Image):
        return value.convert("RGB"), None
    if isinstance(value, list):
        for item in value:
            path = Path(str(item))
            if path.is_file():
                return Image.open(path).convert("RGB"), None
    return None, "클립보드에 사진이 없습니다."


def camera_view(watching: bool, row: dict | None) -> tuple[str, str]:
    """This window's watch only. No extra camera probe, no push."""
    if not watching:
        return "카메라 대기", "#57534e"
    state = (row or {}).get("camera")
    if state == "ok":
        return "카메라 연결됨", "#14532d"
    if state == "down":
        return "카메라 끊김", "#9b1c1c"
    return "카메라 확인 중", "#57534e"


def format_status(row: dict | None) -> str:
    if not row:
        return "판정 없음"
    err = "오류 " + str(row.get("reason") or "") + "\n" if row.get("error") else ""
    return (
        f"{row.get('ts', '')}  {row.get('action', '')}\n"
        f"알림 {row.get('should_alert')}  규칙 {row.get('rule')}\n"
        f"아기 {row.get('baby_present')}  얼굴 {row.get('face_visible')}\n"
        f"움직임 {row.get('motion')}  건너뜀 {row.get('skipped')}\n"
        f"{err}{'' if row.get('error') else (row.get('reason') or '')}"
    )


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), 0.3):
            return True
    except OSError:
        return False


def other_kind(kind: str) -> str:
    return "qwen" if kind == "jev" else "jev"


def server_argv(kind: str) -> list[str]:
    if kind == "jev":
        return [str(IMAJEV_PY), str(DIR / "imajev_serve.py")]
    if kind == "qwen":
        return [
            str(LLAMA),
            "-m", str(QWEN_DIR / "Qwen3-VL-4B-Instruct-Q4_K_M.gguf"),
            "--mmproj", str(QWEN_DIR / "mmproj-F16.gguf"),
            "-ngl", "99", "--mmproj-offload", "--image-min-tokens", "1024",
            "--host", "127.0.0.1", "--port", "8080", "-c", "4096",
        ]
    raise ValueError(kind)


def server_missing(kind: str) -> str | None:
    argv = server_argv(kind)
    if not Path(argv[0]).is_file():
        return f"실행 파일이 없습니다: {argv[0]}"
    return None


def can_start_server(kind: str, own_open: bool, other_open: bool) -> str | None:
    if own_open:
        return "이미 켜져 있습니다."
    if other_open:
        return "다른 모델 서버가 켜져 있습니다. 8GB라 같이 못 올립니다."
    return server_missing(kind)


def watch_argv(send: bool) -> list[str]:
    cmd = [sys.executable, str(DIR / "watch.py"), "--rtsp"]
    if send:
        cmd.append("--send")
    return cmd


def listening_pid(port: int) -> int | None:
    out = subprocess.check_output(["netstat", "-ano", "-p", "tcp"], text=True, errors="replace")
    for line in out.splitlines():
        if "LISTENING" not in line:
            continue
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(f":{port}"):
            try:
                return int(parts[-1])
            except ValueError:
                return None
    return None


def process_command(pid: int) -> str:
    out = subprocess.check_output(
        ["powershell", "-NoProfile", "-Command", f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"],
        text=True, errors="replace",
    )
    return out.strip()


def allowed_stop(kind: str, command: str) -> bool:
    if kind == "jev":
        return "imajev_serve.py" in command
    return "llama-server" in command.replace("\\", "/")


def read_status(path: Path = STATUS) -> dict | None:
    if not path.is_file():
        return None
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return row if isinstance(row, dict) else None


class Tray:
    """System-tray icon. Left click toggles the window. Right click opens or quits."""

    def __init__(self, on_toggle, on_quit) -> None:
        import win32con
        import win32gui

        self.win32con = win32con
        self.win32gui = win32gui
        self.on_toggle = on_toggle
        self.on_quit = on_quit
        self.msg = win32con.WM_USER + 20
        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = self._proc
        wc.lpszClassName = "CribTrayMsg"
        try:
            atom = win32gui.RegisterClass(wc)
        except win32gui.error:
            atom = wc.lpszClassName
        self.hwnd = win32gui.CreateWindow(atom, "CribTrayMsg", 0, 0, 0, 0, 0, 0, 0, 0, None)
        flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
        icon = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
        self.nid = (self.hwnd, 1, flags, self.msg, icon, "아기 감시")
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, self.nid)

    def pump(self) -> None:
        self.win32gui.PumpWaitingMessages()

    def remove(self) -> None:
        self.win32gui.Shell_NotifyIcon(self.win32gui.NIM_DELETE, self.nid)
        self.win32gui.DestroyWindow(self.hwnd)

    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == self.msg and lparam == self.win32con.WM_LBUTTONUP:
            self.on_toggle()
        elif msg == self.msg and lparam == self.win32con.WM_RBUTTONUP:
            self._menu()
        return self.win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _menu(self) -> None:
        menu = self.win32gui.CreatePopupMenu()
        self.win32gui.AppendMenu(menu, self.win32con.MF_STRING, 1, "열기")
        self.win32gui.AppendMenu(menu, self.win32con.MF_STRING, 2, "종료")
        x, y = self.win32gui.GetCursorPos()
        self.win32gui.SetForegroundWindow(self.hwnd)
        cmd = self.win32gui.TrackPopupMenu(
            menu, self.win32con.TPM_LEFTALIGN | self.win32con.TPM_RETURNCMD | self.win32con.TPM_NONOTIFY,
            x, y, 0, self.hwnd, None,
        )
        self.win32gui.PostMessage(self.hwnd, self.win32con.WM_NULL, 0, 0)
        self.win32gui.DestroyMenu(menu)
        if cmd == 1:
            self.on_toggle()
        elif cmd == 2:
            self.on_quit()


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.kind = tk.StringVar(value=model_kind() if model_kind() in PORTS else "jev")
        self.send = tk.BooleanVar(value=False)
        self.note = tk.StringVar(value="")
        self.watch_proc: subprocess.Popen | None = None
        self.server_proc: subprocess.Popen | None = None
        self.server_kind: str | None = None
        self._logs: list = []
        self._photo = None
        self._photo_mtime = 0.0
        self._hold_preview = False
        self._hold_at = 0.0
        self._status_hold = False
        self._testing = False
        self.tray: Tray | None = None
        root.title("아기 감시")
        root.protocol("WM_DELETE_WINDOW", self.to_tray)
        self._build()
        try:
            self.tray = Tray(self.toggle_tray, self.quit_app)
        except Exception as exc:
            self.note.set(f"트레이를 만들지 못했습니다: {type(exc).__name__}")
        self.refresh()

    def _build(self) -> None:
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        ttk.Radiobutton(top, text="jev", variable=self.kind, value="jev", command=self.on_model).pack(side="left")
        ttk.Radiobutton(top, text="qwen", variable=self.kind, value="qwen", command=self.on_model).pack(side="left")
        ttk.Button(top, text="서버 시작", command=self.start_server).pack(side="left", padx=6)
        ttk.Button(top, text="서버 중지", command=self.stop_server).pack(side="left")
        self.server_label = ttk.Label(top, text="")
        self.server_label.pack(side="left", padx=8)
        self.camera_label = tk.Label(top, text="카메라 대기", fg="#57534e")
        self.camera_label.pack(side="left", padx=8)

        mid = ttk.Frame(self.root, padding=(8, 0, 8, 4))
        mid.pack(fill="x")
        ttk.Button(mid, text="감시 시작", command=self.start_watch).pack(side="left")
        ttk.Button(mid, text="감시 중지", command=self.stop_watch).pack(side="left", padx=6)
        ttk.Checkbutton(mid, text="푸시", variable=self.send).pack(side="left")
        ttk.Button(mid, text="트레이로", command=self.to_tray).pack(side="left", padx=6)
        ttk.Button(mid, text="종료", command=self.quit_app).pack(side="left")
        ttk.Button(mid, text="파일 테스트", command=self.test_file).pack(side="left", padx=6)
        ttk.Button(mid, text="붙여넣기", command=self.test_clipboard).pack(side="left")
        self.root.bind("<Control-v>", lambda _e: self.test_clipboard())

        self.headline = tk.Label(self.root, text="판정 없음", justify="left", anchor="w", padx=12, font=("Segoe UI", 28, "bold"))
        self.headline.pack(fill="x")
        self.detail = tk.Label(self.root, text="", justify="left", anchor="w", padx=12, font=("Segoe UI", 16), wraplength=860)
        self.detail.pack(fill="x")
        self.image = tk.Label(self.root, anchor="w", padx=8)
        self.image.pack(fill="x")
        ttk.Label(self.root, textvariable=self.note, anchor="w", padding=(8, 2)).pack(fill="x")
        ttk.Label(self.root, text="기록 (글자만, 최근 50)", anchor="w", padding=(8, 2)).pack(fill="x")
        self.history = tk.Listbox(self.root, height=5, activestyle="none")
        self.history.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        ttk.Label(
            self.root,
            text="의료기기 아님. 종료는 이 창이 켠 감시만 끕니다. 서버는 남습니다. 푸시에는 사진이 없습니다.",
            anchor="w", padding=(8, 0, 8, 8),
        ).pack(fill="x")

    def _spawn(self, argv: list[str], log_name: str, env: dict | None = None) -> subprocess.Popen:
        (DIR / "frames").mkdir(parents=True, exist_ok=True)
        handle = open(DIR / "frames" / log_name, "ab")
        self._logs.append(handle)
        return subprocess.Popen(
            argv, cwd=str(DIR), env=env, stdout=handle, stderr=subprocess.STDOUT,
            creationflags=_NO_WINDOW,
        )

    def start_server(self) -> None:
        if self.server_proc and self.server_proc.poll() is None:
            self.note.set("서버를 켜는 중입니다. 8GB라 다른 서버는 같이 못 올립니다.")
            return
        kind = self.kind.get()
        reason = can_start_server(kind, port_open(PORTS[kind]), port_open(PORTS[other_kind(kind)]))
        if reason:
            self.note.set(reason)
            return
        self.server_proc = self._spawn(server_argv(kind), f"gui-{kind}.log")
        self.server_kind = kind
        self.note.set(f"{kind} 서버를 켜는 중입니다.")

    def stop_server(self) -> None:
        kind = self.kind.get()
        if self.server_proc and self.server_proc.poll() is None and self.server_kind == kind:
            self._stop_proc(self.server_proc)
            self.server_proc = None
            self.note.set("이 창이 켠 서버를 중지했습니다.")
            return
        pid = listening_pid(PORTS[kind])
        if pid is None:
            self.note.set("서버가 꺼져 있습니다.")
            return
        command = process_command(pid)
        if not allowed_stop(kind, command):
            self.note.set("이 포트의 프로세스가 모델 서버가 아니라서 중지하지 않았습니다.")
            return
        subprocess.run(["taskkill", "/PID", str(pid), "/T"], check=False, creationflags=_NO_WINDOW)
        self.note.set("모델 서버를 중지했습니다.")

    def _stop_proc(self, proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    def start_watch(self) -> None:
        if self.watch_proc and self.watch_proc.poll() is None:
            self.note.set("감시가 이미 켜져 있습니다.")
            return
        if not port_open(PORTS[self.kind.get()]):
            self.note.set("서버가 꺼져 있어서 감시를 시작하지 않습니다.")
            return
        env = os.environ.copy()
        env["CRIB_MODEL"] = self.kind.get()
        self.watch_proc = self._spawn(watch_argv(self.send.get()), "gui-watch.log", env)
        self.note.set("감시를 시작했습니다." + (" 푸시 켜짐." if self.send.get() else " 푸시 꺼짐."))

    def stop_watch(self) -> None:
        if self.watch_proc and self.watch_proc.poll() is None:
            self._stop_proc(self.watch_proc)
            self.watch_proc = None
            self.note.set("감시를 중지했습니다.")
        else:
            self.note.set("이 창이 켠 감시가 없습니다.")

    def on_model(self) -> None:
        running = self.watch_proc and self.watch_proc.poll() is None
        if running:
            self._stop_proc(self.watch_proc)
            self.watch_proc = None
        if running and port_open(PORTS[self.kind.get()]):
            self.start_watch()
        elif running:
            self.note.set("모델을 바꿨습니다. 서버가 꺼져 있어 감시는 중지됐습니다.")

    def to_tray(self) -> None:
        if self.tray is None:
            self.note.set("트레이가 없습니다.")
            return
        self.root.withdraw()

    def toggle_tray(self) -> None:
        if self.root.state() == "withdrawn":
            self.root.deiconify()
            self.root.lift()
        else:
            self.root.withdraw()

    def quit_app(self) -> None:
        if self.watch_proc and self.watch_proc.poll() is None:
            self._stop_proc(self.watch_proc)
            self.watch_proc = None
        if self.tray is not None:
            self.tray.remove()
            self.tray = None
        self.root.destroy()

    def refresh(self) -> None:
        kind = self.kind.get()
        up = port_open(PORTS[kind])
        self.server_label.configure(text=f"{kind} {PORTS[kind]} " + ("켜짐" if up else "꺼짐"))
        watching = self.watch_proc is not None and self.watch_proc.poll() is None
        cam_text, cam_color = camera_view(watching, read_status())
        self.camera_label.configure(text=cam_text, fg=cam_color)
        if self._status_hold and not (STATUS.is_file() and STATUS.stat().st_mtime > self._hold_at):
            pass
        else:
            self._status_hold = False
            self._apply_verdict(read_status())
        self._preview()
        self._history()
        if self.tray is not None:
            self.tray.pump()
        self.root.after(1000, self.refresh)

    def _preview(self) -> None:
        if self._hold_preview and not (FRAME.is_file() and FRAME.stat().st_mtime > self._hold_at):
            return
        self._hold_preview = False
        if not FRAME.is_file():
            self.image.configure(image="", text="사진 없음")
            return
        mtime = FRAME.stat().st_mtime
        if mtime == self._photo_mtime:
            return
        image = Image.open(FRAME).convert("RGB")
        self._photo = ImageTk.PhotoImage(self._fit(image))
        self._photo_mtime = mtime
        self.image.configure(image=self._photo, text="")

    def _fit(self, image: Image.Image) -> Image.Image:
        w, h = image.size
        long_edge = max(w, h)
        target = PREVIEW if long_edge > PREVIEW else max(long_edge, 560)
        scale = target / long_edge
        if abs(scale - 1) < 0.02:
            return image
        return image.resize((int(w * scale), int(h * scale)), Image.Resampling.BILINEAR)

    def _show(self, image: Image.Image) -> None:
        self._photo = ImageTk.PhotoImage(self._fit(image))
        self.image.configure(image=self._photo, text="")
        self._hold_preview = True
        self._hold_at = time.time()
        self._status_hold = True

    def test_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, filetypes=[("images", "*.jpg *.jpeg *.png *.webp"), ("all", "*.*")]
        )
        if not path:
            return
        self._run_test(Image.open(path).convert("RGB"), Path(path).name)

    def test_clipboard(self) -> None:
        from PIL import ImageGrab

        image, err = image_from_clipboard(ImageGrab.grabclipboard())
        if image is None:
            self.note.set(err or "클립보드에 사진이 없습니다.")
            return
        self._run_test(image)

    def _run_test(self, image: Image.Image, name: str | None = None) -> None:
        if self._testing:
            self.note.set("테스트가 아직 돌아가는 중입니다.")
            return
        if not port_open(PORTS[self.kind.get()]):
            self.note.set("서버가 꺼져 있어서 테스트하지 않습니다.")
            return
        self._testing = True
        self._show(image)
        self._apply_verdict({"error": True, "reason": "판정 중"})
        self.headline.configure(text="판정 중", fg="#333333")
        self.note.set("폰으로는 보내지 않습니다.")
        kind = self.kind.get()

        def work() -> None:
            from judge import ask

            prev = os.environ.get("CRIB_MODEL")
            os.environ["CRIB_MODEL"] = kind
            try:
                result = ask(image)
                fail = None
                log_judgment(result, kind, "gui-test", image, name or "clipboard")  # never raises
            except Exception as exc:
                result = None
                fail = f"{type(exc).__name__}: {exc}"
            finally:
                if prev is None:
                    os.environ.pop("CRIB_MODEL", None)
                else:
                    os.environ["CRIB_MODEL"] = prev

            def done() -> None:
                self._testing = False
                if fail:
                    self.headline.configure(text="판정 실패", fg="#9b1c1c")
                    self.detail.configure(text=fail)
                else:
                    self._apply_verdict(result, hold=True)
                self.note.set("테스트입니다. 푸시하지 않았고, 감시 기록에도 넣지 않았습니다.")

            self.root.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def _apply_verdict(self, row: dict | None, hold: bool = False) -> None:
        head, detail = verdict_text(row)
        alert = bool(row and row.get("should_alert"))
        self.headline.configure(text=head, fg="#9b1c1c" if alert else "#14532d")
        self.detail.configure(text=detail)
        if hold:
            self._status_hold = True
            self._hold_at = time.time()

    def _history(self) -> None:
        rows = [format_tick(row) for row in tail_ticks(LOG)]
        current = self.history.get(0, "end")
        if list(current) == rows:
            return
        self.history.delete(0, "end")
        for row in rows:
            self.history.insert("end", row)


def _check() -> None:
    sample = DIR / "frames" / "_gui_check.jsonl"
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_text('{"ts":"01:02:03","should_alert":false,"rule":null,"action":"quiet"}\nnot-json\n', encoding="utf-8")
    rows = tail_ticks(sample, 50)
    assert len(rows) == 1 and format_tick(rows[0]).startswith("01:02:03")
    sample.unlink()
    assert "imajev_serve.py" in " ".join(server_argv("jev"))
    assert "llama-server" in server_argv("qwen")[0]
    assert can_start_server("jev", True, False) == "이미 켜져 있습니다."
    assert can_start_server("jev", False, True).startswith("다른 모델")
    assert allowed_stop("jev", r"D:\Dev\imajev\.venv\python.exe C:\_AX\baby-monitor\imajev_serve.py")
    assert not allowed_stop("jev", "python.exe watch.py --rtsp")
    assert not port_open(9)
    assert "--send" not in watch_argv(False) and "--send" in watch_argv(True)
    blank, err = image_from_clipboard(None)
    assert blank is None and err
    img, err = image_from_clipboard(Image.new("RGB", (8, 8), (1, 2, 3)))
    assert err is None and img is not None and img.size == (8, 8)
    missing, err = image_from_clipboard([r"C:\_AX\baby-monitor\no-such-photo.jpg"])
    assert missing is None
    head, detail = verdict_text({"should_alert": False, "baby_present": True, "face_visible": False, "rule": None, "reason": "x"})
    assert head == "알림 없음" and "아기 있음" in detail and "얼굴 안 보임" in detail
    assert "입코 가림 0.76" in verdict_text({"should_alert": False, "scores": {"face_cover": 0.764}})[1]
    got = verdict_text({"should_alert": False, "scores": {"face_down": 0.35},
                        "face_down_parts": {"face_down_cheek": 0.2, "face_down_back": 0.72, "face_down_belly": 0.35}})[1]
    assert "엎드림 0.35  (볼 0.20 / 등 0.72 / 배 0.35)" in got
    assert "(" not in verdict_text({"should_alert": False, "scores": {"face_down": 0.35}})[1]
    assert "(" not in verdict_text({"should_alert": False, "scores": {"face_down": 0.35}, "face_down_parts": "x"})[1]
    assert "(" not in verdict_text({"should_alert": False, "scores": {"face_down": 0.35}, "face_down_parts": {"face_down_back": None}})[1]
    assert camera_view(False, {"camera": "ok"})[0] == "카메라 대기"
    assert camera_view(True, {"camera": "ok"}) == ("카메라 연결됨", "#14532d")
    assert camera_view(True, {"camera": "down"})[0] == "카메라 끊김"
    assert camera_view(True, {})[0] == "카메라 확인 중"
    print("ok crib-gui")


def main() -> None:
    if "--check" in sys.argv:
        _check()
        return
    root = tk.Tk()
    root.geometry("980x920")
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
