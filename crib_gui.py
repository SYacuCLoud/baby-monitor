"""Small local window for the crib watch. Not a medical device.

Starts watch.py and the selected model server. The photo stays in this window.
ntfy still gets text only, and only if 푸시 is on. Secrets in rtsp.env and
ntfy.env are not shown or edited.

"Jev 서버" panel: local (default) or a remote Jev-compatible web API server (e.g. Colab). The logic lives in
remote_settings.py (settings file crib_remote.env, validation, connection test); this file only
draws it. The token is masked (show toggle) and only stored in crib_remote.env (0600 best effort).
Remote is used by the photo tests; live watch uses it only with the extra checkbox
"실시간 감시에도 원격 사용". The tkinter UI was NOT run by the author (no tkinter on the dev box).
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

import proc_stop
import remote_settings
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

        self._build_jev_panel()

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

    def _build_jev_panel(self) -> None:
        """Thin tkinter part of the 'Jev 서버' settings. All logic is in remote_settings.py."""
        saved = remote_settings.load()
        self.r_backend = tk.StringVar(value="remote" if saved.backend == "remote" else "local")
        self.r_url = tk.StringVar(value=saved.url)
        self.r_token = tk.StringVar(value=saved.token)
        self.r_live = tk.BooleanVar(value=saved.remote_live)
        self.r_show = tk.BooleanVar(value=False)
        self.r_model = tk.StringVar(value="모델: -")
        self.r_result = tk.StringVar(value="")
        self.r_status = tk.StringVar(value="")
        box = ttk.LabelFrame(self.root, text="Jev 서버", padding=6)
        box.pack(fill="x", padx=8, pady=(0, 4))
        row1 = ttk.Frame(box)
        row1.pack(fill="x")
        ttk.Radiobutton(row1, text="로컬(기본)", variable=self.r_backend, value="local").pack(side="left")
        ttk.Radiobutton(row1, text="원격 서버", variable=self.r_backend, value="remote").pack(side="left", padx=8)
        ttk.Label(row1, textvariable=self.r_model).pack(side="left", padx=12)
        row2 = ttk.Frame(box)
        row2.pack(fill="x", pady=2)
        ttk.Label(row2, text="주소").pack(side="left")
        ttk.Entry(row2, textvariable=self.r_url, width=46).pack(side="left", padx=(4, 12))
        ttk.Label(row2, text="토큰").pack(side="left")
        self.r_token_entry = ttk.Entry(row2, textvariable=self.r_token, width=30, show="*")
        self.r_token_entry.pack(side="left", padx=4)
        ttk.Checkbutton(row2, text="보이기", variable=self.r_show, command=self.toggle_token).pack(side="left")
        row3 = ttk.Frame(box)
        row3.pack(fill="x")
        ttk.Checkbutton(row3, text="실시간 감시에도 원격 사용", variable=self.r_live).pack(side="left")
        tk.Label(row3, text="체크하면 감시 중인 아기 사진이 이 주소의 서버로 나갑니다. 끊기면 '판정 불가' 알림이 갑니다.",
                 fg="#9b1c1c").pack(side="left", padx=8)
        row4 = ttk.Frame(box)
        row4.pack(fill="x", pady=2)
        ttk.Button(row4, text="저장", command=self.save_remote).pack(side="left")
        ttk.Button(row4, text="연결 테스트", command=self.test_remote).pack(side="left", padx=6)
        ttk.Label(row4, textvariable=self.r_result).pack(side="left", padx=6)
        tk.Label(box, anchor="w", justify="left", fg="#9b1c1c",
                 text="사진이 이 주소로 나갑니다 (사진 테스트, 점수 측정, 위 칸을 체크한 실시간 감시). 연결 테스트 자체는 사진을 보내지 않습니다.\n"
                      "localhost와 사설 IP(192.168.x.x 등)는 http와 토큰 없음이 가능하고, 그 밖의 주소는 https와 토큰이 필요합니다."
                 ).pack(fill="x")
        self.r_status_label = tk.Label(box, textvariable=self.r_status, anchor="w", justify="left", fg="#57534e")
        self.r_status_label.pack(fill="x")
        ttk.Label(box, text="저장해야 적용됩니다. 사진 테스트는 저장된 설정을 씁니다. 환경 변수(CRIB_JEV_*)가 파일보다 우선합니다.",
                  foreground="#57534e").pack(fill="x")

    def toggle_token(self) -> None:
        self.r_token_entry.configure(show="" if self.r_show.get() else "*")

    def _form(self) -> remote_settings.Settings:
        return remote_settings.settings_from_form(self.r_backend.get(), self.r_url.get(), self.r_token.get(),
                                                  "", self.r_live.get())

    def save_remote(self) -> None:
        form = self._form()
        saved = remote_settings.load()
        # keep a hand-edited timeout from the file; the panel has no field for it
        form = remote_settings.Settings(form.backend, form.url, form.token, saved.timeout, form.remote_live)
        try:
            remote_settings.save(form)
        except remote_settings.SettingsError as exc:
            self.note.set(f"저장하지 않았습니다: {exc}")
            return
        except OSError as exc:
            self.note.set(f"저장하지 못했습니다: {type(exc).__name__}")
            return
        self.note.set("Jev 서버 설정을 저장했습니다. 감시 중이면 다음 판정부터 적용됩니다.")
        self._refresh_backend()

    def test_remote(self) -> None:
        """GET /v1/models through the same client code as judging, off the UI thread."""
        form = self._form()
        self.r_result.set("연결 확인 중...")
        # the form is tested as typed; 'local' tests the loopback server
        settings = form if form.backend == "remote" else remote_settings.Settings()

        def work() -> None:
            res = remote_settings.test_connection(settings)

            def done() -> None:
                self.r_result.set(res["message"])
                self.r_model.set("모델: " + (res["model"] or "-") if res["ok"] else "모델: -")

            self.root.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def _refresh_backend(self) -> None:
        settings = remote_settings.load()
        text = remote_settings.status_line(settings, self.kind.get())
        over = remote_settings.env_overrides()
        if over and self.kind.get() == "jev":
            text += "  [환경 변수 우선: " + ", ".join(over) + "]"
        self.r_status.set(text)
        remote = self.kind.get() == "jev" and remote_settings.uses_remote(settings, False)
        self.r_status_label.configure(fg="#9b1c1c" if remote else "#14532d")

    def _remote_for(self, live: bool) -> bool:
        """Would a call from the photo test (live=False) or the live watch (live=True) go to the remote Jev?"""
        return self.kind.get() == "jev" and remote_settings.uses_remote(remote_settings.load(), live)

    def _spawn(self, argv: list[str], log_name: str, env: dict | None = None) -> subprocess.Popen:
        (DIR / "frames").mkdir(parents=True, exist_ok=True)
        handle = open(DIR / "frames" / log_name, "ab")
        self._logs.append(handle)
        return subprocess.Popen(
            argv, cwd=str(DIR), env=env, stdout=handle, stderr=subprocess.STDOUT,
            **proc_stop.spawn_kwargs(),
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
        try:
            self.note.set(self._stop_server())
        except Exception as exc:  # a Tk callback would only print this to stderr: show it instead
            self.note.set(f"서버를 중지하지 못했습니다: {type(exc).__name__}: {exc}")

    def _stop_server(self) -> str:
        """Returns the message to show. 'stopped' is only claimed once the port is really closed."""
        kind = self.kind.get()
        port = PORTS[kind]
        if self.server_proc and self.server_proc.poll() is None and self.server_kind == kind:
            ok = self._stop_proc(self.server_proc)
            self.server_proc = None
            if not ok:
                return "이 창이 켠 서버가 종료되지 않았습니다. 작업 관리자에서 직접 끝내 주세요."
            if not proc_stop.wait_port_closed(port, 5.0, port_open):
                return self._still_open(kind, port)
            return "이 창이 켠 서버를 중지했습니다."
        pid = listening_pid(port)
        if pid is None:
            if kind == "jev":
                remote = self._switch_to_local()
                if remote:
                    return remote
            return "서버가 꺼져 있습니다."
        command = process_command(pid)
        if not allowed_stop(kind, command):
            return "이 포트의 프로세스가 모델 서버가 아니라서 중지하지 않았습니다."
        # /F: a windowless server ignores the polite WM_CLOSE; /T: also the children (venv launcher -> python)
        proc_stop.kill_pid_tree(pid, True)
        if not proc_stop.wait_port_closed(port, 5.0, port_open):
            return self._still_open(kind, port, "이 창이 켠 서버가 아니라서 권한 등으로 끝내지 못했을 수 있습니다. ")
        return "모델 서버를 중지했습니다."

    def _still_open(self, kind: str, port: int, hint: str = "") -> str:
        return f"{kind} 서버를 껐지만 포트 {port}이(가) 아직 열려 있습니다. {hint}작업 관리자에서 직접 끝내 주세요."

    def _switch_to_local(self) -> str | None:
        """Remote Jev has no local process: 'off' means going back to local. None if not in remote mode."""
        settings = remote_settings.load()
        if settings.backend != "remote":
            return None
        if "CRIB_JEV_BACKEND" in remote_settings.env_overrides():
            return "원격 모드는 환경 변수 CRIB_JEV_BACKEND로 정해져 있어 GUI에서 바꿀 수 없습니다. 꺼야 할 로컬 프로세스도 없습니다."
        # Never switch silently while the watch may be using the remote: the watch re-reads the settings
        # for every judgment, so it would suddenly fall back to a local server that may be off.
        if self.watch_proc and self.watch_proc.poll() is None:
            return "감시 중에는 원격 설정을 바꾸지 않습니다. 먼저 감시를 멈춘 뒤 다시 눌러 주세요."
        if settings.remote_live:
            return ("'실시간 감시에도 원격 사용'이 켜져 있어 원격 설정을 바꾸지 않습니다. 다른 곳에서 감시가 돌고 있을 수 있습니다. "
                    "감시를 멈추고 체크를 끈 뒤 저장하고 다시 눌러 주세요.")
        saved = remote_settings.Settings("local", settings.url, settings.token, settings.timeout, settings.remote_live)
        try:
            remote_settings.save(saved)
        except (remote_settings.SettingsError, OSError) as exc:
            return f"원격 연결을 해제하지 못했습니다: {type(exc).__name__}"
        self.r_backend.set("local")
        self._refresh_backend()
        return "원격 Jev 연결을 해제했습니다(로컬로 전환, 끌 프로세스 없음)."

    def _stop_proc(self, proc: subprocess.Popen) -> bool:
        """Stop proc and its whole process tree (terminate/kill, taskkill /T /F on Windows)."""
        return proc_stop.stop_process_tree(proc)

    def start_watch(self) -> None:
        if self.watch_proc and self.watch_proc.poll() is None:
            self.note.set("감시가 이미 켜져 있습니다.")
            return
        remote_live = self._remote_for(True)
        if not remote_live and not port_open(PORTS[self.kind.get()]):
            self.note.set("서버가 꺼져 있어서 감시를 시작하지 않습니다.")
            return
        env = os.environ.copy()
        env["CRIB_MODEL"] = self.kind.get()
        self.watch_proc = self._spawn(watch_argv(self.send.get()), "gui-watch.log", env)
        self.note.set("감시를 시작했습니다." + (" 푸시 켜짐." if self.send.get() else " 푸시 꺼짐.")
                      + (f" 원격 서버 사용 중: {remote_settings.photo_destination(remote_settings.load().url)}." if remote_live else ""))

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
        if running and (self._remote_for(True) or port_open(PORTS[self.kind.get()])):
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
        self._refresh_backend()
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
        remote = self._remote_for(False)
        if not remote and not port_open(PORTS[self.kind.get()]):
            self.note.set("서버가 꺼져 있어서 테스트하지 않습니다.")
            return
        self._testing = True
        self._show(image)
        self._apply_verdict({"error": True, "reason": "판정 중"})
        self.headline.configure(text="판정 중", fg="#333333")
        self.note.set("폰으로는 보내지 않습니다." + (f" 원격 서버로 보냅니다: {remote_settings.photo_destination(remote_settings.load().url)}." if remote else ""))
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
    # Jev 서버 panel logic (the tkinter widgets themselves are not exercised here)
    st = remote_settings.Settings("remote", "https://abc-def.trycloudflare.com", "tok_0123456789", "", False)
    assert remote_settings.uses_remote(st, False) and not remote_settings.uses_remote(st, True)
    line = remote_settings.status_line(st)
    assert "원격" in line and "abc-def" not in line and "tok_0123" not in line
    assert "로컬" in remote_settings.status_line(remote_settings.Settings())
    lan = remote_settings.Settings("remote", "http://192.168.1.20:9000/api", "", "", True)
    assert remote_settings.validate(lan) == lan and "같은 네트워크" in remote_settings.status_line(lan)
    assert "192" not in remote_settings.status_line(lan) and "집 밖" in remote_settings.photo_destination(st.url)
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
