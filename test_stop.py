"""Headless self-test for stopping the Jev server. python test_stop.py

No display, no real model server: a tiny dummy python process tree stands in for
`venv python.exe launcher -> real python`, and tkinter is stubbed so crib_gui's stop logic can run.
Windows-only behaviour (taskkill /T /F) is checked by the command line it would run (not run here).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import time
import types
from pathlib import Path
from unittest import mock

import proc_stop
import remote_settings as R

PARENT = textwrap.dedent("""
    import subprocess, sys, time
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    print(child.pid, flush=True)
    time.sleep(120)
""")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a zombie still answers kill(0)
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def gone(pid: int, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)


def test_tree_killed() -> None:
    proc = subprocess.Popen([sys.executable, "-c", PARENT], stdout=subprocess.PIPE, text=True, **proc_stop.spawn_kwargs())
    child = int(proc.stdout.readline())
    assert alive(proc.pid) and alive(child)
    assert proc_stop.stop_process_tree(proc, grace=2.0)
    assert proc.poll() is not None, "leader still running"
    assert gone(child), "child (the 'real python' behind the launcher) survived: only the leader was stopped"
    assert proc_stop.stop_process_tree(proc)  # already stopped: no error


def test_sigterm_ignored_is_killed() -> None:
    code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print(1, flush=True); time.sleep(120)"
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, **proc_stop.spawn_kwargs())
    proc.stdout.readline()
    assert proc_stop.stop_process_tree(proc, grace=0.5)
    assert proc.poll() is not None


def test_external_pid_killed() -> None:
    proc = subprocess.Popen([sys.executable, "-c", PARENT], stdout=subprocess.PIPE, text=True, **proc_stop.spawn_kwargs())
    child = int(proc.stdout.readline())
    assert proc_stop.kill_pid_tree(proc.pid, True)
    proc.wait(timeout=3)
    assert gone(child)


def test_never_signals_own_group() -> None:
    # a pid in our own process group must be signalled alone, never via killpg
    with mock.patch("os.killpg") as killpg:
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        proc_stop.kill_pid_tree(sleeper.pid, True)
        sleeper.wait(timeout=3)
        killpg.assert_not_called()


def test_windows_command_line() -> None:
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        return types.SimpleNamespace(returncode=0)

    with mock.patch.object(proc_stop, "WINDOWS", True):
        assert proc_stop.kill_pid_tree(4321, True, run)
        proc = mock.Mock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        proc.pid = 99
        assert proc_stop.stop_process_tree(proc, run=run)
        assert proc_stop.spawn_kwargs() == {"creationflags": 0x08000000}
    assert calls[0][0] == ["taskkill", "/PID", "4321", "/T", "/F"]
    assert calls[1][0] == ["taskkill", "/PID", "99", "/T", "/F"]
    proc.terminate.assert_not_called()


def test_wait_port_closed() -> None:
    assert proc_stop.wait_port_closed(1, 1.0, lambda p: False)
    t0 = time.monotonic()
    assert not proc_stop.wait_port_closed(1, 0.4, lambda p: True)
    assert time.monotonic() - t0 >= 0.4


def load_gui():
    """crib_gui with tkinter stubbed (this box has none, and no display is needed)."""
    tk = types.ModuleType("tkinter")
    tk.Tk = object
    tk.filedialog = mock.MagicMock()
    tk.ttk = mock.MagicMock()
    tk.StringVar = tk.BooleanVar = mock.MagicMock
    sys.modules.setdefault("tkinter", tk)
    sys.modules.setdefault("tkinter.filedialog", tk.filedialog)
    sys.modules.setdefault("tkinter.ttk", tk.ttk)
    import crib_gui
    return crib_gui


def make_app(gui, kind="jev"):
    app = object.__new__(gui.App)
    app.kind = mock.Mock(get=lambda: kind)
    app.note = mock.Mock()
    app.server_proc = None
    app.server_kind = None
    app.watch_proc = None
    app.r_backend = mock.Mock()
    app._refresh_backend = mock.Mock()
    return app


def test_gui_stop_logic() -> None:
    gui = load_gui()
    with tempfile.TemporaryDirectory() as tmp:
        env_file = Path(tmp) / "crib_remote.env"
        env = {"CRIB_REMOTE_ENV": str(env_file)}
        env.update({k: "" for k in R.KEYS})
        with mock.patch.dict(os.environ, env):
            # 1) started by this window: tree stopped, success only after the port closed
            app = make_app(gui)
            proc = subprocess.Popen([sys.executable, "-c", PARENT], stdout=subprocess.PIPE, text=True, **proc_stop.spawn_kwargs())
            child = int(proc.stdout.readline())
            app.server_proc, app.server_kind = proc, "jev"
            with mock.patch.object(gui, "port_open", return_value=False):
                msg = app._stop_server()
            assert "중지했습니다" in msg and proc.poll() is not None and gone(child), msg
            assert app.server_proc is None
            # 2) port still open after the kill: must NOT say stopped
            proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **proc_stop.spawn_kwargs())
            app.server_proc, app.server_kind = proc, "jev"
            with mock.patch.object(gui, "port_open", return_value=True), \
                    mock.patch.object(proc_stop, "wait_port_closed", lambda p, t, f: not f(p)):
                msg = app._stop_server()
            assert "아직 열려 있습니다" in msg and "중지했습니다" not in msg, msg
            # 3) started outside the GUI: taskkill-style kill with force, verified by the port
            app = make_app(gui)
            with mock.patch.object(gui, "listening_pid", return_value=777), \
                    mock.patch.object(gui, "process_command", return_value=r"python imajev_serve.py"), \
                    mock.patch.object(proc_stop, "kill_pid_tree", return_value=True) as kill, \
                    mock.patch.object(gui, "port_open", return_value=False):
                msg = app._stop_server()
            kill.assert_called_once_with(777, True)
            assert msg == "모델 서버를 중지했습니다.", msg
            with mock.patch.object(gui, "listening_pid", return_value=777), \
                    mock.patch.object(gui, "process_command", return_value=r"python imajev_serve.py"), \
                    mock.patch.object(proc_stop, "kill_pid_tree", return_value=False), \
                    mock.patch.object(proc_stop, "wait_port_closed", return_value=False):
                msg = app._stop_server()
            assert "아직 열려 있습니다" in msg, msg
            # 4) not our model server on the port: refuse
            with mock.patch.object(gui, "listening_pid", return_value=5), \
                    mock.patch.object(gui, "process_command", return_value="nginx.exe"), \
                    mock.patch.object(proc_stop, "kill_pid_tree") as kill:
                assert "중지하지 않았습니다" in app._stop_server()
            kill.assert_not_called()
            # 5) exception inside is shown, not swallowed by Tk
            with mock.patch.object(gui, "listening_pid", side_effect=FileNotFoundError("netstat")):
                app.stop_server()
            assert "중지하지 못했습니다" in app.note.set.call_args[0][0]
            # 6) remote mode, no watch, live off: no process, 'off' = back to local; URL/token kept
            R.save(R.Settings("remote", "https://abc.trycloudflare.com", "tok_0123456789", "", False), env_file)
            app = make_app(gui)
            with mock.patch.object(gui, "listening_pid", return_value=None):
                msg = app._stop_server()
            assert "해제" in msg, msg
            now = R.load()
            assert now.backend == "local"
            app.r_backend.set.assert_called_with("local")
            text = env_file.read_text()
            assert "CRIB_JEV_BACKEND=local" in text and "https://abc.trycloudflare.com" in text and "tok_0123456789" in text
            # 6b) remote mode and a watch is running: settings file must stay byte-identical
            for live in (False, True):
                R.save(R.Settings("remote", "https://abc.trycloudflare.com", "tok_0123456789", "", live), env_file)
                before = env_file.read_bytes()
                app = make_app(gui)
                app.watch_proc = mock.Mock(poll=lambda: None)
                with mock.patch.object(gui, "listening_pid", return_value=None):
                    msg = app._stop_server()
                assert "감시 중에는 원격 설정을 바꾸지 않습니다" in msg, msg
                assert env_file.read_bytes() == before and R.load().backend == "remote"
                app.r_backend.set.assert_not_called()
            # 6c) remote-live checkbox on, no GUI watch (an outside watch may be live): do not switch either
            R.save(R.Settings("remote", "https://abc.trycloudflare.com", "tok_0123456789", "", True), env_file)
            before = env_file.read_bytes()
            app = make_app(gui)
            with mock.patch.object(gui, "listening_pid", return_value=None):
                msg = app._stop_server()
            assert "바꾸지 않습니다" in msg and env_file.read_bytes() == before, msg
            # 6d) watch already exited (poll() is not None) counts as not running
            R.save(R.Settings("remote", "https://abc.trycloudflare.com", "tok_0123456789", "", False), env_file)
            app = make_app(gui)
            app.watch_proc = mock.Mock(poll=lambda: 1)
            with mock.patch.object(gui, "listening_pid", return_value=None):
                assert "해제" in app._stop_server()
            # 7) plain local, nothing running
            with mock.patch.object(gui, "listening_pid", return_value=None):
                assert app._stop_server() == "서버가 꺼져 있습니다."
            # 8) env forces remote: say so, do not touch the file
            before = env_file.read_text()
            with mock.patch.dict(os.environ, {"CRIB_JEV_BACKEND": "remote", "CRIB_JEV_URL": "https://abc.trycloudflare.com",
                                              "CRIB_JEV_TOKEN": "tok_0123456789"}), \
                    mock.patch.object(gui, "listening_pid", return_value=None):
                assert "환경 변수" in app._stop_server()
            assert env_file.read_text() == before


def main() -> None:
    if sys.platform == "win32":
        print("skip POSIX process-tree cases on Windows; running the mocked ones")
    tests = [test_windows_command_line, test_wait_port_closed]
    if sys.platform != "win32":
        tests += [test_tree_killed, test_sigterm_ignored_is_killed, test_external_pid_killed,
                  test_never_signals_own_group, test_gui_stop_logic]
    for t in tests:
        t()
        print("  ok", t.__name__)
    print("ok stop")


if __name__ == "__main__":
    main()
