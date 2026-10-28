"""Small local window for the crib watch. Not a medical device.

Starts watch.py and the selected model server. The photo stays in this window.
ntfy still gets text only, and only if 푸시 is on. Secrets in rtsp.env and
ntfy.env are not shown or edited. Local only.
Left: model, watch, test. Right: photo and tick log.
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

import remote_settings
from judge import model_kind
from jev_protocol import ALERT_AT
from score_log import log_judgment

DIR = Path(__file__).resolve().parent
FRAME = DIR / "frames" / "latest.jpg"
STATUS = DIR / "frames" / "status.json"
LOG = DIR / "frames" / "ticks.jsonl"
PORTS = {"jev": 8090, "qwen": 8080, "lightning": 8741}
HISTORY = 50
PREVIEW = 720

IMAJEV_PY = Path(os.environ.get("IMAJEV_PY", str(DIR / "runtime" / "imajev" / ".venv" / "Scripts" / "python.exe")))
LLAMA = Path(os.environ.get("LLAMA_SERVER", str(DIR / "runtime" / "llama-server" / "llama-server.exe")))
QWEN_DIR = Path(os.environ.get("QWEN_DIR", str(DIR / "runtime" / "models" / "Qwen3-VL-4B")))
LIGHTNING_PY = Path(os.environ.get("LIGHTNING_PY", sys.executable))
LIGHTNING_GGUF_DIR = Path(os.environ.get("LIGHTNING_GGUF_DIR", r"D:\Dev\_Models\h2o-lightning-4b-gguf"))

_NO_WINDOW = 0x08000000
_NEW_GROUP = 0x00000200


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
        order = ("face_cover", "climbing", "baby_present", "adult_present", "face_visible")
        names = {"baby_present": "아기", "adult_present": "어른", "face_visible": "얼굴", **RULE_KO}
        nums = [f"{names.get(key, key)} {float(scores[key]):.2f}" for key in order if key in scores]
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
    """Legacy helper (jev/qwen only). Prefer any_other_open for 3+ models."""
    return "qwen" if kind == "jev" else "jev"


def any_other_open(kind: str, ports: dict[str, bool]) -> bool:
    return any(up for name, up in ports.items() if name != kind)


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
    if kind == "lightning":
        return [str(LIGHTNING_PY), str(DIR / "lightning_serve.py")]
    raise ValueError(kind)


def can_start_server(kind: str, own_open: bool, other_open: bool) -> str | None:
    if own_open:
        return "이미 켜져 있습니다."
    if other_open:
        return "다른 모델 서버가 켜져 있습니다. 8GB라 같이 못 올립니다."
    return server_missing(kind)


def server_missing(kind: str) -> str | None:
    argv = server_argv(kind)
    if not Path(argv[0]).is_file():
        return f"실행 파일이 없습니다: {argv[0]}"
    if kind == "lightning":
        model = LIGHTNING_GGUF_DIR / "h2o-lightning-4b-Q4_K_M.gguf"
        mmproj = LIGHTNING_GGUF_DIR / "mmproj-lightning-f16.gguf"
        llama = Path(os.environ.get("LIGHTNING_LLAMA_SERVER", str(LIGHTNING_GGUF_DIR / "llama-b11946" / "llama-server.exe")))
        for path, label in ((model, "Lightning Q4 GGUF"), (mmproj, "Lightning mmproj"),
                            (llama, "llama-server b11946")):
            if not path.is_file():
                return f"{label} 없음: {path}"
    return None


def watch_argv(send: bool) -> list[str]:
    cmd = [sys.executable, str(DIR / "watch.py"), "--rtsp"]
    if send:
        cmd.append("--send")
    return cmd


def listening_pid(port: int) -> int | None:
    out = subprocess.check_output(
        ["netstat", "-ano", "-p", "tcp"],
        text=True, errors="replace", timeout=2,
    )
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
        ["powershell", "-NoProfile", "-Command",
         f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"],
        text=True, errors="replace", timeout=3,
    )
    return out.strip()


def allowed_stop(kind: str, command: str) -> bool:
    if kind == "jev":
        return "imajev_serve.py" in command
    if kind == "lightning":
        cmd = command.replace(chr(92), "/")
        return "lightning_serve.py" in cmd or "h2o_lightning_llamacpp.py" in cmd or "llama-server" in cmd
    return "llama-server" in command.replace(chr(92), "/")


def kill_tree(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        check=False, creationflags=_NO_WINDOW,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8,
    )


def control_state(server: str, watching: bool, busy: bool, testing: bool) -> dict[str, bool]:
    idle = not busy and not testing
    return {
        "server_start": idle and server in ("꺼짐", "실패"),
        "server_stop": idle and server in ("켜짐", "켜는 중"),
        "watch_start": idle and server == "켜짐" and not watching,
        "watch_stop": idle and watching,
        "radios": idle and server not in ("켜는 중", "끄는 중"),
        "test": idle and server == "켜짐",
    }


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
        self._busy = False
        self._stopping_server = False
        self._alive = True
        self._ports = {"jev": False, "qwen": False, "lightning": False}
        self._server_state = "꺼짐"
        self.tray: Tray | None = None
        root.title("아기 감시")
        root.protocol("WM_DELETE_WINDOW", self.to_tray)
        self._build()
        try:
            self.tray = Tray(self.toggle_tray, self.quit_app)
        except Exception as exc:
            self.note.set(f"트레이를 만들지 못했습니다: {type(exc).__name__}")
        threading.Thread(target=self._poll_ports, daemon=True).start()
        self.refresh()

    def _build(self) -> None:
        body = ttk.Panedwindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=8)
        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=1)
        body.add(right, weight=1)

        model = ttk.LabelFrame(left, text="모델", padding=6)
        model.pack(fill="x")
        row = ttk.Frame(model)
        row.pack(fill="x")
        ttk.Radiobutton(row, text="Jev", variable=self.kind, value="jev", command=self.on_model).pack(side="left")
        ttk.Radiobutton(row, text="Qwen", variable=self.kind, value="qwen", command=self.on_model).pack(side="left", padx=(8, 0))
        ttk.Radiobutton(row, text="Lightning", variable=self.kind, value="lightning", command=self.on_model).pack(side="left", padx=(8, 0))
        self.radio_jev = row.winfo_children()[0]
        self.radio_qwen = row.winfo_children()[1]
        self.radio_lightning = row.winfo_children()[2]
        self.quant_label = ttk.Label(model, text="Lightning: Q4_K_M (4bit GGUF, llama b11946, 8GB용)")
        self.quant_label.pack(anchor="w", pady=(4, 0))
        srv = ttk.Frame(model)
        srv.pack(fill="x", pady=(6, 0))
        self.btn_server_start = ttk.Button(srv, text="서버 시작", command=self.start_server)
        self.btn_server_start.pack(side="left")
        self.btn_server_stop = ttk.Button(srv, text="중지", command=self.stop_server)
        self.btn_server_stop.pack(side="left", padx=6)
        self.server_label = ttk.Label(srv, text="")
        self.server_label.pack(side="left", padx=8)

        watch = ttk.LabelFrame(left, text="감시", padding=6)
        watch.pack(fill="x", pady=(8, 0))
        wrow = ttk.Frame(watch)
        wrow.pack(fill="x")
        self.btn_watch_start = ttk.Button(wrow, text="시작", command=self.start_watch)
        self.btn_watch_start.pack(side="left")
        self.btn_watch_stop = ttk.Button(wrow, text="중지", command=self.stop_watch)
        self.btn_watch_stop.pack(side="left", padx=6)
        ttk.Checkbutton(wrow, text="푸시", variable=self.send).pack(side="left", padx=(8, 0))
        self.camera_label = tk.Label(watch, text="카메라 대기", fg="#57534e", anchor="w")
        self.camera_label.pack(fill="x", pady=(4, 0))

        test = ttk.LabelFrame(left, text="한 장 테스트", padding=6)
        test.pack(fill="x", pady=(8, 0))
        trow = ttk.Frame(test)
        trow.pack(fill="x")
        self.btn_test_file = ttk.Button(trow, text="파일", command=self.test_file)
        self.btn_test_file.pack(side="left")
        self.btn_test_clip = ttk.Button(trow, text="붙여넣기", command=self.test_clipboard)
        self.btn_test_clip.pack(side="left", padx=6)
        ttk.Label(trow, text="Ctrl+V", foreground="#57534e").pack(side="left", padx=8)
        self.root.bind("<Control-v>", lambda _e: self.test_clipboard())

        vrow = ttk.Frame(left)
        vrow.pack(fill="x", pady=(12, 0))
        self.btn_copy = ttk.Button(vrow, text="복사", command=self.copy_verdict, width=6)
        self.btn_copy.pack(side="right", anchor="n")
        texts = ttk.Frame(vrow)
        texts.pack(side="left", fill="both", expand=True)
        self.headline = self._readonly_text(texts, height=1, font=("Segoe UI", 22, "bold"), fg="#14532d")
        self.headline.pack(fill="x")
        self.detail = self._readonly_text(texts, height=8, font=("Segoe UI", 12), fg="#1c1917")
        self.detail.pack(fill="x")
        self._fill(self.headline, "판정 없음")
        ttk.Label(left, textvariable=self.note, anchor="w").pack(fill="x", pady=(8, 0))
        ttk.Label(
            left,
            text="의료기기 아님. 창을 닫으면 트레이로 갑니다. 종료는 트레이 메뉴. 푸시에는 사진이 없습니다.",
            foreground="#57534e", wraplength=520, justify="left",
        ).pack(fill="x", pady=(8, 0))

        self.image = tk.Label(right, anchor="n")
        self.image.pack(fill="both", expand=True)
        hrow = ttk.Frame(right)
        hrow.pack(fill="x", pady=(8, 0))
        ttk.Label(hrow, text="기록 (글자만, 최근 50)", anchor="w").pack(side="left")
        ttk.Button(hrow, text="복사", command=self.copy_history, width=6).pack(side="right")
        self.history = tk.Listbox(right, height=12, activestyle="none", selectmode="extended", exportselection=False)
        self.history.pack(fill="both", expand=True)
        self.history.bind("<Control-c>", lambda _e: self.copy_history())
        self.history.bind("<Control-C>", lambda _e: self.copy_history())

    def _remote_for(self, live: bool) -> bool:
        return False

    def _readonly_text(self, parent, height: int, font, fg: str) -> tk.Text:
        widget = tk.Text(
            parent, height=height, wrap="word", font=font, fg=fg,
            bg=self.root.cget("bg"), insertbackground=fg,
            relief="flat", bd=0, highlightthickness=0, padx=0, pady=0,
            cursor="xterm", takefocus=True,
        )
        widget.bind("<Key>", self._readonly_key)
        widget.bind("<<Paste>>", lambda _e: "break")
        widget.bind("<<Cut>>", lambda _e: "break")
        return widget

    @staticmethod
    def _readonly_key(event: tk.Event) -> str | None:
        if event.keysym in {
            "Left", "Right", "Up", "Down", "Home", "End", "Next", "Prior",
            "Shift_L", "Shift_R", "Control_L", "Control_R",
        }:
            return None
        if event.state & 0x4 and event.keysym.lower() in {"c", "a"}:
            return None
        return "break"

    def _fill(self, widget: tk.Text, text: str, fg: str | None = None) -> None:
        if fg:
            widget.configure(fg=fg)
        widget.delete("1.0", "end")
        widget.insert("1.0", text)

    def _clipboard(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()

    def copy_verdict(self) -> None:
        head = self.headline.get("1.0", "end-1c").strip()
        detail = self.detail.get("1.0", "end-1c").strip()
        blob = head if not detail else f"{head}\n{detail}"
        self._clipboard(blob)
        self.note.set("판정 글을 복사했습니다.")

    def copy_history(self) -> None:
        sel = self.history.curselection()
        lines = [self.history.get(i) for i in sel] if sel else list(self.history.get(0, "end"))
        if not lines:
            self.note.set("복사할 기록이 없습니다.")
            return
        self._clipboard("\n".join(lines))
        self.note.set("기록을 복사했습니다.")

    def _spawn(self, argv: list[str], log_name: str, env: dict | None = None) -> subprocess.Popen:
        (DIR / "frames").mkdir(parents=True, exist_ok=True)
        handle = open(DIR / "frames" / log_name, "ab")
        self._logs.append(handle)
        return subprocess.Popen(
            argv, cwd=str(DIR), env=env, stdout=handle, stderr=subprocess.STDOUT,
            creationflags=_NO_WINDOW | _NEW_GROUP,
        )

    def _server_tag(self) -> str:
        return self.kind.get()

    def _port_up(self, kind: str) -> bool:
        return bool(self._ports.get(kind))

    def _poll_ports(self) -> None:
        while self._alive:
            for kind, port in PORTS.items():
                try:
                    self._ports[kind] = port_open(port)
                except OSError:
                    self._ports[kind] = False
            time.sleep(0.4)

    def _watching(self) -> bool:
        return self.watch_proc is not None and self.watch_proc.poll() is None

    def _sync_controls(self) -> None:
        state = control_state(self._server_state, self._watching(), self._busy, self._testing)
        self.btn_server_start.configure(state="normal" if state["server_start"] else "disabled")
        self.btn_server_stop.configure(state="normal" if state["server_stop"] else "disabled")
        self.btn_watch_start.configure(state="normal" if state["watch_start"] else "disabled")
        self.btn_watch_stop.configure(state="normal" if state["watch_stop"] else "disabled")
        radios = "normal" if state["radios"] else "disabled"
        self.radio_jev.configure(state=radios)
        self.radio_qwen.configure(state=radios)
        self.radio_lightning.configure(state=radios)
        test = "normal" if state["test"] else "disabled"
        self.btn_test_file.configure(state=test)
        self.btn_test_clip.configure(state=test)

    def _paint_server(self) -> None:
        kind = self.kind.get()
        tag = self._server_tag()
        up = self._port_up(kind)
        proc = self.server_proc
        alive = proc is not None and proc.poll() is None
        dead = proc is not None and proc.poll() is not None
        if self._stopping_server:
            state = "끄는 중"
        elif up:
            state = "켜짐"
        elif alive:
            state = "켜는 중"
        elif dead:
            state = "실패"
            self.server_proc = None
        else:
            state = "꺼짐"
        self._server_state = state
        self.server_label.configure(text=f"{tag} {state}")
        self._sync_controls()

    def start_server(self) -> None:
        if self._busy:
            return
        kind = self.kind.get()
        alive = self.server_proc is not None and self.server_proc.poll() is None
        if alive or self._port_up(kind):
            self._paint_server()
            return
        if any_other_open(kind, self._ports):
            self.note.set("다른 모델 서버가 켜져 있습니다. 8GB라 같이 못 올립니다.")
            return
        reason = server_missing(kind)
        if reason:
            self.note.set(reason)
            self._paint_server()
            return
        self.server_proc = self._spawn(server_argv(kind), f"gui-{kind}.log")
        self.server_kind = kind
        self._paint_server()

    def stop_server(self) -> None:
        if self._busy:
            return
        kind = self.kind.get()
        proc = self.server_proc
        self._busy = True
        self._stopping_server = True
        self._paint_server()
        threading.Thread(target=self._stop_server_bg, args=(kind, proc), daemon=True).start()

    def _stop_server_bg(self, kind: str, proc: subprocess.Popen | None) -> None:
        note = None
        try:
            if proc is not None and proc.poll() is None:
                kill_tree(proc.pid)
            else:
                pid = listening_pid(PORTS[kind])
                if pid is not None:
                    command = process_command(pid)
                    if allowed_stop(kind, command):
                        kill_tree(pid)
                    else:
                        note = "이 포트의 프로세스가 모델 서버가 아니라서 중지하지 않았습니다."
        except (OSError, subprocess.SubprocessError) as exc:
            note = f"중지 실패: {type(exc).__name__}"
        self.root.after(0, lambda: self._stop_server_done(note))

    def _stop_server_done(self, note: str | None) -> None:
        self._busy = False
        self._stopping_server = False
        self.server_proc = None
        if note:
            self.note.set(note)
        self._paint_server()

    def start_watch(self) -> None:
        if self._busy or self._watching():
            return
        if not self._port_up(self.kind.get()):
            self.note.set("서버가 꺼져 있어서 감시를 시작하지 않습니다.")
            return
        env = os.environ.copy()
        env["CRIB_MODEL"] = self.kind.get()
        self.watch_proc = self._spawn(watch_argv(self.send.get()), "gui-watch.log", env)
        self.note.set("감시를 시작했습니다." + (" 푸시 켜짐." if self.send.get() else " 푸시 꺼짐."))
        self._sync_controls()

    def stop_watch(self) -> None:
        if self._busy or not self._watching():
            return
        self._busy = True
        self._sync_controls()
        proc = self.watch_proc
        threading.Thread(target=self._stop_watch_bg, args=(proc,), daemon=True).start()

    def _stop_watch_bg(self, proc: subprocess.Popen) -> None:
        try:
            if proc.poll() is None:
                kill_tree(proc.pid)
        except (OSError, subprocess.SubprocessError):
            pass
        self.root.after(0, self._stop_watch_done)

    def _stop_watch_done(self) -> None:
        self._busy = False
        self.watch_proc = None
        self.note.set("감시를 중지했습니다.")
        self._sync_controls()

    def on_model(self) -> None:
        self._paint_server()
        if self._watching():
            self.stop_watch()

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
        self._alive = False
        if self.watch_proc is not None and self.watch_proc.poll() is None:
            kill_tree(self.watch_proc.pid)
            self.watch_proc = None
        if self.server_proc is not None and self.server_proc.poll() is None:
            kill_tree(self.server_proc.pid)
            self.server_proc = None
        if self.tray is not None:
            self.tray.remove()
            self.tray = None
        self.root.destroy()

    def refresh(self) -> None:
        self._paint_server()
        watching = self._watching()
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
        if not self._port_up(self.kind.get()):
            self.note.set("서버가 꺼져 있어서 테스트하지 않습니다.")
            return
        self._testing = True
        self._sync_controls()
        self._show(image)
        self._apply_verdict({"error": True, "reason": "판정 중"})
        self._fill(self.headline, "판정 중", "#333333")
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
                self._sync_controls()
                if fail:
                    self._fill(self.headline, "판정 실패", "#9b1c1c")
                    self._fill(self.detail, fail)
                else:
                    self._apply_verdict(result, hold=True)
                self.note.set("테스트입니다. 푸시하지 않았고, 감시 기록에도 넣지 않았습니다.")

            self.root.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def _apply_verdict(self, row: dict | None, hold: bool = False) -> None:
        head, detail = verdict_text(row)
        alert = bool(row and row.get("should_alert"))
        self._fill(self.headline, head, "#9b1c1c" if alert else "#14532d")
        self._fill(self.detail, detail)
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
    assert "lightning_serve.py" in " ".join(server_argv("lightning"))
    assert can_start_server("jev", True, False) == "이미 켜져 있습니다."
    assert can_start_server("jev", False, True).startswith("다른 모델")
    assert allowed_stop("jev", str(DIR / "imajev_serve.py"))
    assert not allowed_stop("jev", str(DIR / "d1_omni_serve.py"))
    assert not allowed_stop("jev", "python.exe watch.py --rtsp")
    assert allowed_stop("lightning", str(DIR / "lightning_serve.py"))
    assert any_other_open("lightning", {"jev": True, "qwen": False, "lightning": False})
    assert not any_other_open("lightning", {"jev": False, "qwen": False, "lightning": True})
    assert not port_open(9)
    assert "--send" not in watch_argv(False) and "--send" in watch_argv(True)
    blank, err = image_from_clipboard(None)
    assert blank is None and err
    img, err = image_from_clipboard(Image.new("RGB", (8, 8), (1, 2, 3)))
    assert err is None and img is not None and img.size == (8, 8)
    missing, err = image_from_clipboard([str(DIR / "no-such-photo.jpg")])
    assert missing is None
    head, detail = verdict_text({"should_alert": False, "baby_present": True, "face_visible": False, "rule": None, "reason": "x"})
    assert head == "알림 없음" and "아기 있음" in detail and "얼굴 안 보임" in detail
    assert "입코 가림 0.76" in verdict_text({"should_alert": False, "scores": {"face_cover": 0.764}})[1]
    assert camera_view(False, {"camera": "ok"})[0] == "카메라 대기"
    assert camera_view(True, {"camera": "ok"}) == ("카메라 연결됨", "#14532d")
    assert camera_view(True, {"camera": "down"})[0] == "카메라 끊김"
    assert camera_view(True, {})[0] == "카메라 확인 중"
    # Jev 서버 panel logic (the tkinter widgets themselves are not exercised here)
    st = remote_settings.Settings("remote", "https://abc-def.trycloudflare.com", "tok_0123456789", "", False)
    assert remote_settings.uses_remote(st, False) and not remote_settings.uses_remote(st, True)
    line = remote_settings.status_line(st)
    assert "원격" in line and "abc-def" not in line and "tok_0123" not in line
    assert "로컬" in remote_settings.status_line(remote_settings.Settings())
    off = control_state("꺼짐", False, False, False)
    assert off["server_start"] and not off["server_stop"] and not off["watch_start"] and not off["test"]
    on = control_state("켜짐", False, False, False)
    assert not on["server_start"] and on["server_stop"] and on["watch_start"] and on["test"]
    busy = control_state("끄는 중", False, True, False)
    assert not busy["server_start"] and not busy["server_stop"] and not busy["radios"]
    print("ok crib-gui")


def main() -> None:
    if "--check" in sys.argv:
        _check()
        return
    root = tk.Tk()
    root.geometry("1180x780")
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
