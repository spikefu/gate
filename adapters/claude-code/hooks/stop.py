#!/usr/bin/env python3
"""Claude Code Stop hook: run gate on the changed files and hand failures back.

Thin on purpose. All judgement lives in `gate check`; this only translates its
result into the hook protocol. Standard library only, so it runs under any
python3 regardless of where gate itself is installed.

Opt-in per repo: without a gate.toml in the project, it does nothing.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Claude Code caps consecutive blocks itself, but a failure the agent cannot
# fix (a flaky suite, a missing service) should reach the human sooner.
MAX_BLOCKS = int(os.environ.get("GATE_HOOK_MAX_BLOCKS", "3"))
MAX_LISTED = 30


def main() -> int:
    event = json.load(sys.stdin)
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or ".")
    if not (project / "gate.toml").is_file():
        return 0
    gate = os.environ.get("GATE_BIN") or shutil.which("gate")
    if gate is None:
        return _note("gate.toml is present but the `gate` command is not on PATH; nothing was checked.")
    proc = subprocess.run([gate, "check", "--changed", "--json"], cwd=project, capture_output=True, text=True)
    counter = _counter(event)
    if proc.returncode == 0:
        counter.unlink(missing_ok=True)
        return 0
    if proc.returncode != 1:
        return _note(f"gate could not run: {proc.stderr.strip() or proc.stdout.strip()}")
    blocks = _bump(counter, fresh=not event.get("stop_hook_active"))
    report = json.loads(proc.stdout)
    if blocks > MAX_BLOCKS:
        counter.unlink(missing_ok=True)
        return _note(f"gate still fails after {MAX_BLOCKS} attempts; leaving it for you.\n{_summary(report)}")
    print(json.dumps({"decision": "block", "reason": _summary(report)}))
    return 0


def _summary(report: dict) -> str:
    blocking = [f for f in report["findings"] if f["severity"] == "fail" and not f["baselined"]]
    lines = ["gate check failed on the files you changed. Fix these, do not weaken the rules or the tests:"]
    for f in blocking[:MAX_LISTED]:
        where = f["path"] + (f":{f['line']}" if f.get("line") else "")
        symbol = f" {f['symbol']}" if f.get("symbol") else ""
        lines.append(f"- [{f['check']}] {where}{symbol}: {f['message']}")
        if f.get("detail"):
            lines += ["    " + ln for ln in f["detail"].splitlines()[-15:]]
    if len(blocking) > MAX_LISTED:
        lines.append(f"... and {len(blocking) - MAX_LISTED} more")
    if report.get("strict"):
        lines += [f"- [{c}] could not run: {r}" for c, r in report["skipped"].items()]
    return "\n".join(lines)


def _counter(event: dict) -> Path:
    return Path(tempfile.gettempdir()) / f"gate-stop-{event.get('session_id', 'unknown')}"


def _bump(counter: Path, fresh: bool) -> int:
    prior = 0 if fresh or not counter.is_file() else int(counter.read_text() or 0)
    counter.write_text(str(prior + 1))
    return prior + 1


def _note(message: str) -> int:
    """Tell the human without blocking: a non-zero, non-2 exit is a non-blocking hook error."""
    print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
