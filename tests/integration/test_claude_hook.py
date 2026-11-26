"""The Claude Code Stop hook: blocks on failures, stays quiet otherwise, gives up after a limit."""

import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import branchy

HOOK = Path(__file__).parents[2] / "adapters/claude-code/hooks/stop.py"
GATE = Path(sys.executable).parent / "gate"
CONFIG = '[profiles]\nfast = ["complexity"]\n'


def stop(repo, session, active=False, max_blocks="3"):
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo.root), "GATE_BIN": str(GATE), "GATE_HOOK_MAX_BLOCKS": max_blocks}
    event = {"session_id": session, "cwd": str(repo.root), "stop_hook_active": active}
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), env=env, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def test_does_nothing_without_gate_toml(repo, request):
    repo.write("a.py", branchy("f", 20))
    assert stop(repo, request.node.name) == (0, "", "")


def test_passes_silently_when_changed_files_are_clean(repo, request):
    repo.write("gate.toml", CONFIG)
    repo.commit()
    repo.write("a.py", branchy("f", 2))
    assert stop(repo, request.node.name) == (0, "", "")


def test_blocks_with_the_finding_when_a_changed_file_fails(repo, request):
    repo.write("gate.toml", CONFIG)
    repo.commit()
    repo.write("a.py", branchy("f", 20))
    code, out, _ = stop(repo, request.node.name)
    decision = json.loads(out)
    assert code == 0 and decision["decision"] == "block"
    assert "a.py:1 f: CCN 21 > 15" in decision["reason"]
    assert "If a finding is wrong" in decision["reason"] and "Do not work around it" in decision["reason"]


def test_gives_up_and_tells_the_human_after_the_limit(repo, request):
    repo.write("gate.toml", CONFIG)
    repo.commit()
    repo.write("a.py", branchy("f", 20))
    session = request.node.name
    assert json.loads(stop(repo, session, max_blocks="2")[1])["decision"] == "block"
    assert json.loads(stop(repo, session, active=True, max_blocks="2")[1])["decision"] == "block"
    code, out, err = stop(repo, session, active=True, max_blocks="2")
    assert (code, out) == (1, "") and "still fails after 2 attempts" in err
    # A fresh stop (not a continuation) starts counting again.
    assert json.loads(stop(repo, session, max_blocks="2")[1])["decision"] == "block"


def test_config_error_is_reported_without_blocking(repo, request):
    repo.write("gate.toml", "[nonsense]\n")
    repo.commit()
    code, out, err = stop(repo, request.node.name)
    assert (code, out) == (1, "") and "nonsense" in err


def test_block_message_leaves_out_findings_the_baseline_already_covers(repo, request):
    repo.write("gate.toml", CONFIG)
    repo.write("a.py", branchy("legacy", 20))
    repo.commit()
    subprocess.run([str(GATE), "baseline", "--accept"], cwd=repo.root, check=True, capture_output=True)
    repo.write("a.py", branchy("legacy", 20) + branchy("fresh", 22))
    _, out, _ = stop(repo, request.node.name)
    reason = json.loads(out)["reason"]
    assert "fresh" in reason and "legacy" not in reason


def test_an_oversized_change_is_shown_to_the_human_without_blocking_the_agent(repo, request):
    repo.write("gate.toml", '[change-size]\nmax_lines = 5\n[profiles]\nfast = ["complexity", "change-size"]\n')
    repo.commit()
    repo.write("a.py", "".join(f"x{i} = {i}\n" for i in range(9)))
    code, out, err = stop(repo, request.node.name)
    assert (code, out) == (1, ""), "exit 1 with no decision: shown to the human, the agent is not blocked"
    assert "gate passed, with a note for you" in err and "adds 9 lines of non-test code" in err


def test_other_warnings_do_not_interrupt(repo, request):
    repo.write("gate.toml", CONFIG)
    repo.commit()
    repo.write("a.py", branchy("f", 11))   # complexity warning, not a failure
    assert stop(repo, request.node.name) == (0, "", "")


def test_block_message_carries_the_tools_own_output_so_the_agent_can_act_on_it(repo, request):
    repo.write("gate.toml", '[toolchain]\nlint = "echo \'a.py:3: unused variable total\'; exit 1"\n[profiles]\nfast = ["lint"]\n')
    repo.commit()
    repo.write("a.py", "x = 1\n")
    _, out, _ = stop(repo, request.node.name)
    assert "    a.py:3: unused variable total" in json.loads(out)["reason"]
