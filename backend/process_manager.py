"""Cross-platform Appium server process management.

The previous implementation used `os.setsid` / `os.killpg`, which exist only on
POSIX, so starting Appium raised AttributeError on Windows. This module picks
the right process-group primitives per platform and resolves the executable
name (`appium.cmd` on Windows) before spawning.
"""

import os
import re
import shutil
import signal
import subprocess
import sys
from typing import Optional

from config import LOG_FILE_PATH

IS_WINDOWS = sys.platform.startswith("win")

_process: Optional[subprocess.Popen] = None
_log_handle = None

_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _resolve_executable() -> Optional[str]:
    """Find the appium binary, accounting for the .cmd shim npm installs on Windows."""
    for candidate in ("appium", "appium.cmd", "appium.ps1"):
        path = shutil.which(candidate)
        if path:
            return path
    return None


def _build_env() -> dict:
    env = os.environ.copy()
    if not IS_WINDOWS:
        # Homebrew and /usr/local are commonly missing from a GUI-launched process.
        extra = ["/opt/homebrew/bin", "/usr/local/bin"]
        env["PATH"] = os.pathsep.join([*extra, env.get("PATH", "")])
    return env


def is_process_alive() -> bool:
    return _process is not None and _process.poll() is None


def start() -> tuple[bool, str]:
    """Spawn `appium --allow-cors`. Returns (ok, message)."""
    global _process, _log_handle

    if is_process_alive():
        return True, "Appium process is already running."

    executable = _resolve_executable()
    if executable is None:
        return False, (
            "Appium executable not found on PATH. Install it globally with "
            "`npm install -g appium` and restart the backend."
        )

    try:
        _log_handle = open(LOG_FILE_PATH, "w", encoding="utf-8")

        kwargs = {
            "stdout": _log_handle,
            "stderr": subprocess.STDOUT,
            "env": _build_env(),
            "cwd": os.path.dirname(LOG_FILE_PATH),
        }

        if IS_WINDOWS:
            # A new process group lets us signal the whole tree later.
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True

        _process = subprocess.Popen([executable, "--allow-cors"], **kwargs)
        return True, "Appium server process started."
    except Exception as exc:
        _process = None
        return False, f"Failed to start Appium server: {exc}"


def stop() -> tuple[bool, str]:
    """Terminate the Appium process and everything it spawned."""
    global _process, _log_handle

    if not is_process_alive():
        _process = None
        _close_log()
        return True, "Appium process is not active."

    pid = _process.pid
    try:
        if IS_WINDOWS:
            # taskkill /T walks the child tree; Appium spawns driver subprocesses.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                check=False,
            )
        else:
            os.killpg(os.getpgid(pid), signal.SIGTERM)

        try:
            _process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            if IS_WINDOWS:
                _process.kill()
            else:
                os.killpg(os.getpgid(pid), signal.SIGKILL)

        _process = None
        _close_log()
        return True, "Appium server stopped."
    except Exception as exc:
        try:
            _process.kill()
            _process = None
            _close_log()
            return True, f"Appium server force-terminated (group kill failed: {exc})."
        except Exception as fallback_exc:
            return False, f"Failed to stop Appium server: {exc} / {fallback_exc}"


def _close_log() -> None:
    global _log_handle
    if _log_handle is not None:
        try:
            _log_handle.close()
        except Exception:
            pass
        _log_handle = None


def read_logs(limit: int = 400) -> str:
    if not os.path.exists(LOG_FILE_PATH):
        return "No logs available yet."
    try:
        with open(LOG_FILE_PATH, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-limit:]
        return _ANSI_ESCAPE.sub("", "".join(lines))
    except Exception as exc:
        return f"Error reading log file: {exc}"
