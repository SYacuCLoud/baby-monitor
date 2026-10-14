"""Stop a server process and everything it started. No tkinter, so it can be tested headless.

Why: on Windows the imajev venv's Scripts\\python.exe is a launcher that starts the real python
as a child, so Popen.terminate() only ends the launcher and the child keeps port 8090. And
`taskkill /PID <pid> /T` without /F only posts WM_CLOSE, which a windowless server ignores.
So: Windows -> `taskkill /PID <pid> /T /F` (whole tree); POSIX -> signal the process group.
Callers must not claim "stopped" until wait_port_closed() says the port is really closed.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time

WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000


def spawn_kwargs() -> dict:
    """Popen kwargs: no console window on Windows; own session (= own process group) on POSIX."""
    if WINDOWS:
        return {"creationflags": CREATE_NO_WINDOW}
    return {"start_new_session": True}


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), 0.3):
            return True
    except OSError:
        return False


def wait_port_closed(port: int, timeout: float = 5.0, is_open=_port_open) -> bool:
    """True once nothing listens on the port (polled up to timeout seconds)."""
    end = time.monotonic() + timeout
    while True:
        if not is_open(port):
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(0.2)


def kill_pid_tree(pid: int, force: bool = True, run=subprocess.run) -> bool:
    """Kill pid and its children. Windows: taskkill /T (/F). POSIX: process group, else the pid."""
    if WINDOWS:
        cmd = ["taskkill", "/PID", str(pid), "/T"] + (["/F"] if force else [])
        res = run(cmd, check=False, capture_output=True, creationflags=CREATE_NO_WINDOW)
        return res.returncode == 0
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        pgid = os.getpgid(pid)
    except OSError:
        return False
    try:
        if pgid != os.getpgrp():  # never signal our own group (the GUI itself)
            os.killpg(pgid, sig)
        else:
            os.kill(pid, sig)
    except OSError:
        return False
    return True


def _killpg(pgid: int, sig: int) -> None:
    if pgid == os.getpgrp():
        return
    try:
        os.killpg(pgid, sig)
    except OSError:
        pass


def stop_process_tree(proc: subprocess.Popen, grace: float = 3.0, run=subprocess.run) -> bool:
    """Terminate proc and its whole tree: polite first (POSIX only), then force. True if proc is gone."""
    if proc.poll() is not None:
        return True
    if WINDOWS:
        # taskkill /T /F also takes the real python behind the venv launcher
        kill_pid_tree(proc.pid, True, run)
    else:
        # proc was started with start_new_session=True, so its group id is its pid
        _killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
        # children can outlive the leader, so always sweep the group once more with SIGKILL
        _killpg(proc.pid, signal.SIGKILL)
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            return False
    return True
