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


def run(command: str | list[str], cwd: Path, timeout: int, extra_env: dict[str, str] | None = None) -> tuple[int | None, str]:
    """Return (exit code, combined output). Exit code None means it timed out."""
    env = {**os.environ, "NO_COLOR": "1", "PATH": tool_path(cwd), **(extra_env or {})}
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


def tool_path(root: Path) -> str:
    """PATH with the repo's own tool directories first.

    Why: a repo's linter and test runner live in its virtualenv or
    node_modules. Without this, a command resolves to whatever the machine
    has globally, and the same tree passes on one machine and fails on another.
    """
    own = [d for d in (root / ".venv" / "bin", root / ".venv" / "Scripts", root / "node_modules" / ".bin") if d.is_dir()]
    return os.pathsep.join([*map(str, own), os.environ.get("PATH", "")])


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
