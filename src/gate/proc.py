"""Runs project commands with a timeout that takes the whole process tree.

Killing only the shell on timeout orphans the real work beneath it (a test
runner, a dev server), which then holds ports and memory.
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

POSIX = os.name == "posix"


def run(command: str | list[str], cwd: Path, timeout: int) -> tuple[int | None, str]:
    """Return (exit code, combined output). Exit code None means it timed out."""
    env = {**os.environ, "NO_COLOR": "1"}
    proc = subprocess.Popen(
        command, shell=isinstance(command, str), cwd=cwd, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=POSIX,
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out or ""
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        out, _ = proc.communicate()
        return None, out or ""


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if POSIX:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def tail(text: str, limit: int = 2000) -> str:
    text = text.strip()
    return text if len(text) <= limit else "...\n" + text[-limit:]
