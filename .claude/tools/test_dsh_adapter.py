"""Tests for hooks/dsh_adapter.py and hooks/dsh-hooks.json.

The public surface is the CLI: `python3 dsh_adapter.py <target>` run as a subprocess with a
DSH-shaped payload on stdin. Every run gets its own HOME and its own state dir (the adapter's
DSH_ADAPTER_STATE_DIR seam), so the adapter's state and logs and the hooks' own logs land in tmp,
never in ~/.claude or /tmp/dsh-claude-state-<uid>, and the caller's CLAUDE_* kill switches are
dropped so the real hooks behave the same everywhere. Only the tests of the default state dir and
the Seatbelt ones run without the seam.

Run: cd ~/.claude/tools && uv run --with pytest pytest -p no:cacheprovider test_dsh_adapter.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterator

import pytest

sys.dont_write_bytecode = True

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
ADAPTER = HOOKS / "dsh_adapter.py"
CONFIG = HOOKS / "dsh-hooks.json"
PROJECT_ROOT = "/tmp/dsh-trino-root"  # TRINO_GUARDRAIL_PROJECT_ROOT for trino-guardrail.py

ROLES = [
  "reviewer", "planner", "finding-verifier", "bug-investigator", "test-runner",
  "web-researcher", "docs-researcher", "code-writer-t1", "code-writer-t2", "code-writer-t3",
]
EXEMPT_ROLES = ["reviewer", "web-researcher", "docs-researcher"]  # exempt from the doctrine gate, as in Claude
GATED_ROLES = [role for role in ROLES if role not in EXEMPT_ROLES]
SLICE_ROLES = ["code-writer-t1", "code-writer-t2", "code-writer-t3", "test-runner"]
GENERIC_TOOL = "subagent"  # DSH's delegation tool with no role: Claude runs such an Agent call as general-purpose
SCRIPT_TARGETS = ["trino-guardrail", "check-file-size", "config-protection", "require-orchestration"]
CLOSED_TARGETS = ["trino-guardrail", "config-protection"]
OPEN_TARGETS = ["check-file-size", "require-orchestration", "track-skill", "log-run"]

STATE_ENV = "DSH_ADAPTER_STATE_DIR"
GATE_OFF_ENV = "CLAUDE_ORCHESTRATION_GATE"  # the hook's own kill switch

Result = subprocess.CompletedProcess[bytes]
Payload = dict[str, Any]


def state_dir(home: Path) -> Path:
  """Where make_env points the adapter's state: beside HOME, so a test can wreck HOME and leave the state alone."""
  return home.parent / "dsh-state"


def make_env(home: Path, state: Path | None = None) -> dict[str, str]:
  env = {key: value for key, value in os.environ.items() if not key.startswith("CLAUDE_")}
  env.update(HOME=str(home), PYTHONDONTWRITEBYTECODE="1", **{STATE_ENV: str(state or state_dir(home))})
  env["TRINO_GUARDRAIL_PROJECT_ROOT"] = PROJECT_ROOT
  return env


def default_state_env(home: Path) -> dict[str, str]:
  """make_env without the state seam: the adapter picks its own state dir, as it does under DSH."""
  env = make_env(home)
  del env[STATE_ENV]
  return env


def run_adapter(
  target: str, payload: object, home: Path, adapter: Path = ADAPTER, env: dict[str, str] | None = None
) -> Result:
  stdin = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
  argv = [sys.executable, str(adapter)] + ([target] if target else [])
  return subprocess.run(argv, input=stdin, capture_output=True, env=make_env(home) if env is None else env, timeout=60)


def run_claude_hook(name: str, payload: Payload, home: Path) -> Result:
  """The same hook with a Claude payload and no adapter: the reference the adapter must reproduce."""
  return subprocess.run(
    [sys.executable, str(HOOKS / f"{name}.py")], input=json.dumps(payload).encode("utf-8"),
    capture_output=True, env=make_env(home), timeout=60,
  )


def pre(tool: str, tool_input: Payload, session: str = "sess-1", cwd: str = "/tmp/dsh-project") -> Payload:
  return {
    "session_id": session, "transcript_path": "", "cwd": cwd, "hook_event_name": "PreToolUse",
    "tool_name": tool, "tool_input": tool_input, "tool_use_id": "call-1",
  }


def post(tool: str, tool_input: Payload, response: str, session: str = "sess-1") -> Payload:
  return {**pre(tool, tool_input, session), "hook_event_name": "PostToolUse", "tool_response": response}


def outcome(result: Result) -> str:
  """'exit2', 'silent' (the hook decided nothing) or the permissionDecision it printed."""
  if result.returncode == 2:
    return "exit2"
  assert result.returncode == 0, result.stderr
  if not result.stdout.strip():
    return "silent"
  return str(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"])


def reason(result: Result) -> str:
  return str(json.loads(result.stdout)["hookSpecificOutput"].get("permissionDecisionReason", ""))


def load_hook(filename: str) -> ModuleType:
  spec = importlib.util.spec_from_file_location(filename[:-3].replace("-", "_"), HOOKS / filename)
  assert spec and spec.loader
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


@pytest.fixture
def home(tmp_path: Path) -> Path:
  path = tmp_path / "home"
  path.mkdir()
  return path


@pytest.fixture
def broken_home(tmp_path: Path) -> Path:
  """A HOME that is a file: nothing can be created under it."""
  path = tmp_path / "home-is-a-file"
  path.write_text("x")
  return path


@pytest.fixture
def broken_state(tmp_path: Path) -> Path:
  """A state dir that cannot be created: its parent is a file."""
  blocker = tmp_path / "state-parent-is-a-file"
  blocker.write_text("x")
  return blocker / "state"


@pytest.fixture
def readonly_logs(home: Path) -> Iterator[Path]:
  """HOME/.claude/logs as DSH's hook sandbox leaves it: there, but nothing can be created in it."""
  if os.geteuid() == 0:
    pytest.skip("root ignores directory permissions")
  logs = home / ".claude" / "logs"
  logs.mkdir(parents=True)
  logs.chmod(0o500)
  yield logs
  logs.chmod(0o700)


@pytest.fixture
def project(tmp_path: Path) -> Path:
  root = tmp_path / "project"
  root.mkdir()
  (root / "big.txt").write_text("line\n" * 400)
  (root / "small.txt").write_text("line\n" * 10)
  (root / "ruff.toml").write_text("line-length = 100\n")
  (root / "pyproject.toml").write_text(
    '[project]\ndependencies = ["requests==2.31.0"]\n\n[tool.ruff.lint]\nselect = ["E", "F"]\n'
  )
  return root


@pytest.fixture
def stub_hooks(tmp_path: Path) -> Path:
  """A copy of the adapter in an otherwise empty hooks dir: the hook scripts are whatever a test puts there."""
  directory = tmp_path / "stub-hooks"
  directory.mkdir()
  shutil.copy(ADAPTER, directory / ADAPTER.name)
  return directory


def put_hook(directory: Path, name: str, source: str) -> None:
  (directory / f"{name}.py").write_text(source)


def run_stub(stub_hooks: Path, target: str, payload: object, home: Path) -> Result:
  return run_adapter(target, payload, home, stub_hooks / ADAPTER.name)


ECHO_HOOK = "import sys\nsys.stdout.write(sys.stdin.read())\n"
READ_CALL = pre("read", {"file_path": "/x"})


# --- the real hooks through the adapter: the same decision and output as the Claude hook ---

@dataclass(frozen=True)
class HookCase:
  name: str
  target: str
  dsh_tool: str
  claude_tool: str
  tool_input: Callable[[Path], Payload]
  expected: str


def read_case(name: str, extra: Payload, expected: str, file: str = "big.txt") -> HookCase:
  return HookCase(name, "check-file-size", "read", "Read", lambda p: {"file_path": str(p / file), **extra}, expected)


def edit_case(name: str, file: str, old: str, new: str, expected: str) -> HookCase:
  edit = {"old_string": old, "new_string": new}
  return HookCase(name, "config-protection", "edit", "Edit", lambda p: {"file_path": str(p / file), **edit}, expected)


def trino_case(name: str, tool: str, query: str) -> HookCase:
  return HookCase(name, "trino-guardrail", tool, tool, lambda p: {"query": query}, "deny")


HOOK_CASES = [
  read_case("read-big", {}, "deny"),
  read_case("read-big-limit", {"limit": 50}, "allow"),
  read_case("read-big-offset", {"offset": 10}, "allow"),
  read_case("read-small", {}, "allow", file="small.txt"),
  edit_case("edit-ruff-toml", "ruff.toml", "100", "200", "deny"),
  edit_case("edit-pyproject-tool-section", "pyproject.toml", '["E", "F"]', '["E"]', "deny"),
  edit_case("edit-pyproject-dependency", "pyproject.toml", "2.31.0", "2.32.0", "allow"),
  edit_case("edit-unprotected-file", "small.txt", "line", "row", "allow"),
  HookCase(
    "write-ruff-toml", "config-protection", "write", "Write",
    lambda p: {"file_path": str(p / "ruff.toml"), "content": "line-length = 200\n"}, "deny",
  ),
  HookCase(
    "write-new-config", "config-protection", "write", "Write",
    lambda p: {"file_path": str(p / "fresh" / "ruff.toml"), "content": "x = 1\n"}, "allow",
  ),
  # The shipped hook only scopes Trino to the configured project (the query-level DDL/DML guard lives in that
  # repo's own hook), so outside it a SELECT is refused as firmly as a DROP.
  trino_case("trino-ddl-outside-project", "mcp__trino__execute_query", "DROP TABLE ic.prod.events"),
  trino_case("trino-select-outside-project", "mcp__trino__explain_query", "SELECT 1"),
]


@pytest.mark.parametrize("case", HOOK_CASES, ids=[case.name for case in HOOK_CASES])
def test_hook_decision_and_output_match_the_claude_hook(case: HookCase, home: Path, project: Path) -> None:
  tool_input = case.tool_input(project)
  adapted = run_adapter(case.target, pre(case.dsh_tool, tool_input), home)
  reference = run_claude_hook(case.target, pre(case.claude_tool, tool_input), home)
  assert outcome(adapted) == case.expected, adapted.stdout
  assert adapted.returncode == reference.returncode
  assert adapted.stdout == reference.stdout
  assert adapted.stderr == reference.stderr


def test_deny_reasons_name_what_was_blocked(home: Path, project: Path) -> None:
  big = reason(run_adapter("check-file-size", pre("read", {"file_path": str(project / "big.txt")}), home))
  assert "big.txt" in big and "400" in big
  ruff = {"file_path": str(project / "ruff.toml"), "old_string": "100", "new_string": "9"}
  assert "ruff.toml" in reason(run_adapter("config-protection", pre("edit", ruff), home))
  mixed = {"file_path": str(project / "pyproject.toml"), "old_string": '["E", "F"]', "new_string": '["E"]'}
  assert "tool.ruff.lint" in reason(run_adapter("config-protection", pre("edit", mixed), home))


@pytest.mark.parametrize("query", ["SELECT 1", "DROP TABLE ic.prod.events"])  # DDL too: see HOOK_CASES comment
@pytest.mark.parametrize("cwd", [PROJECT_ROOT, PROJECT_ROOT + "/.claude/worktrees/some-branch"])
def test_trino_hook_defers_inside_project_root(cwd: str, query: str, home: Path) -> None:
  result = run_adapter("trino-guardrail", pre("mcp__trino__execute_query", {"query": query}, cwd=cwd), home)
  assert outcome(result) == "silent"


@pytest.mark.parametrize("override", [{}, {"TRINO_GUARDRAIL_PROJECT_ROOT": ""}], ids=["unset", "empty"])
def test_trino_hook_fails_closed_without_a_project_root(override: dict[str, str], home: Path) -> None:
  env = {key: value for key, value in make_env(home).items() if key != "TRINO_GUARDRAIL_PROJECT_ROOT"}
  call = pre("mcp__trino__execute_query", {"query": "SELECT 1"}, cwd=PROJECT_ROOT)
  result = run_adapter("trino-guardrail", call, home, env={**env, **override})
  assert outcome(result) == "deny"
  assert "TRINO_GUARDRAIL_PROJECT_ROOT" in reason(result)


# --- payload translation, seen by a hook that echoes what it received ---

@pytest.mark.parametrize("dsh_tool,claude_tool", [
  ("read", "Read"), ("write", "Write"), ("edit", "Edit"), ("bash", "Bash"), ("grep", "Grep"),
  ("glob", "Glob"), ("skill", "Skill"), ("web_fetch", "WebFetch"), ("web_search", "WebSearch"),
  ("mcp__trino__execute_query", "mcp__trino__execute_query"), ("mcp__other__tool", "mcp__other__tool"),
])
def test_tool_names_are_translated_and_the_rest_of_the_payload_is_kept(
  dsh_tool: str, claude_tool: str, stub_hooks: Path, home: Path
) -> None:
  put_hook(stub_hooks, "check-file-size", ECHO_HOOK)
  payload = pre(dsh_tool, {"file_path": "/x", "offset": 3}, session="s-7", cwd="/work")
  result = run_stub(stub_hooks, "check-file-size", payload, home)
  assert result.returncode == 0, result.stderr
  assert json.loads(result.stdout) == {**payload, "tool_name": claude_tool}


@pytest.mark.parametrize("target", ["config-protection", "check-file-size"])
def test_an_unmapped_tool_is_an_adapter_failure_not_a_silent_allow(target: str, home: Path) -> None:
  result = run_adapter(target, pre("multi_edit", {"file_path": "/x"}), home)
  assert result.returncode == (2 if target in CLOSED_TARGETS else 0)
  assert b"multi_edit" in result.stderr and result.stdout == b""


@pytest.mark.parametrize("role", ROLES)
def test_a_role_tool_reaches_the_hook_as_an_agent_call(role: str, stub_hooks: Path, home: Path) -> None:
  put_hook(stub_hooks, "require-orchestration", ECHO_HOOK)
  tool_input = {"description": "short title", "prompt": "slice_id: s1\nbody", "run_in_background": True}
  result = run_stub(stub_hooks, "require-orchestration", pre(role, tool_input, session="sess-9"), home)
  seen = json.loads(result.stdout)
  transcript = state_dir(home) / "sess-9.jsonl"
  assert seen["tool_name"] == "Agent"
  assert seen["tool_input"] == {**tool_input, "subagent_type": role}
  assert seen["transcript_path"] == str(transcript)
  assert transcript.is_file() and transcript.read_text() == ""  # exists but empty: no doctrine yet


def test_the_generic_subagent_tool_reaches_the_hook_as_a_general_purpose_agent_call(
  stub_hooks: Path, home: Path
) -> None:
  put_hook(stub_hooks, "require-orchestration", ECHO_HOOK)
  tool_input = {
    "description": "short title", "prompt": "body", "provider": "p", "model": "m",
    "reasoning_effort": "high", "run_in_background": True,
  }
  result = run_stub(stub_hooks, "require-orchestration", pre(GENERIC_TOOL, tool_input, session="sess-9"), home)
  seen = json.loads(result.stdout)
  transcript = state_dir(home) / "sess-9.jsonl"
  assert seen["tool_name"] == "Agent"
  assert seen["tool_input"] == {**tool_input, "subagent_type": "general-purpose"}
  assert seen["transcript_path"] == str(transcript)
  assert transcript.is_file() and transcript.read_text() == ""


# --- require-orchestration over the synthetic per-session transcript ---

def role_call(role: str, prompt: str = "slice_id: dsh-hooks\ndo the work", session: str = "sess-1") -> Payload:
  return pre(role, {"description": "work", "prompt": prompt}, session=session)


def gate(home: Path, role: str, env: dict[str, str] | None = None, **kwargs: str) -> Result:
  return run_adapter("require-orchestration", role_call(role, **kwargs), home, env=env)


def load_doctrine(home: Path, session: str = "sess-1", skill: str = "task-orchestration") -> None:
  result = run_adapter("track-skill", pre("skill", {"name": skill}, session=session), home)
  assert (result.returncode, result.stdout) == (0, b"")


@pytest.mark.parametrize("role", GATED_ROLES)
def test_a_gated_role_is_denied_before_the_doctrine_is_loaded(role: str, home: Path) -> None:
  result = gate(home, role)
  assert outcome(result) == "deny"
  assert "task-orchestration" in reason(result)


@pytest.mark.parametrize("role", EXEMPT_ROLES)
def test_lookup_roles_and_reviewer_need_no_doctrine(role: str, home: Path) -> None:
  assert outcome(gate(home, role, prompt="look this up")) == "allow"


@pytest.mark.parametrize("role", GATED_ROLES)
def test_a_gated_role_passes_after_track_skill(role: str, home: Path) -> None:
  load_doctrine(home)
  assert outcome(gate(home, role)) == "allow"


@pytest.mark.parametrize("role", ROLES)
def test_slice_id_is_required_only_from_the_slice_roles(role: str, home: Path) -> None:
  load_doctrine(home)
  result = gate(home, role, prompt="no slice line here")
  if role in SLICE_ROLES:
    assert outcome(result) == "deny" and "slice_id" in reason(result)
  else:
    assert outcome(result) == "allow"


def test_the_doctrine_is_per_session(home: Path) -> None:
  load_doctrine(home, session="parent")
  assert outcome(gate(home, "planner", session="parent")) == "allow"
  assert outcome(gate(home, "planner", session="child")) == "deny"


@pytest.mark.parametrize("skill,unlocks", [
  ("task-orchestration", True), ("plugin:task-orchestration", True),
  ("code-tier-assessment", False), ("task-orchestration-lite", False), ("", False),
])
def test_only_the_doctrine_skill_unlocks_delegation(skill: str, unlocks: bool, home: Path) -> None:
  load_doctrine(home, skill=skill)
  assert outcome(gate(home, "planner")) == ("allow" if unlocks else "deny")


def test_the_transcript_line_is_one_skill_loaded_recognizes(home: Path) -> None:
  hook = load_hook("require-orchestration.py")
  transcript = state_dir(home) / "sess-1.jsonl"
  gate(home, "planner")  # creates the empty file
  assert transcript.is_file() and hook.skill_loaded(transcript) is False
  load_doctrine(home)
  assert hook.skill_loaded(transcript) is True


def test_a_session_id_cannot_escape_the_state_dir(home: Path) -> None:
  load_doctrine(home, session="../../../../outside /x")
  created = [path for path in home.parent.rglob("*") if path.is_file()]
  assert [path.parent for path in created] == [state_dir(home)]


def test_an_empty_session_id_still_gets_a_state_file(home: Path) -> None:
  load_doctrine(home, session="")
  assert [path.name for path in state_dir(home).iterdir()] == ["no-session.jsonl"]


# --- where the state lives: DSH's hook sandbox lets a hook write only under the project, /tmp and the temp dir ---

def test_the_gate_still_decides_while_the_logs_dir_is_unwritable(home: Path, readonly_logs: Path) -> None:
  assert outcome(gate(home, "planner")) == "deny"
  load_doctrine(home)
  assert outcome(gate(home, "planner")) == "allow"
  assert (state_dir(home) / "sess-1.jsonl").is_file()
  assert list(readonly_logs.iterdir()) == []  # neither the state nor the transcript went under ~/.claude/logs


def test_the_default_state_dir_is_a_private_dir_under_tmp(home: Path) -> None:
  """No seam: /tmp/dsh-claude-state-<uid>, or the temp dir's twin of it when /tmp is closed to the process."""
  name = f"dsh-claude-state-{os.getuid()}"
  candidates = [Path("/tmp") / name, Path(tempfile.gettempdir()) / name]
  session = f"default-root-{uuid.uuid4().hex}"
  try:
    skill = pre("skill", {"name": "task-orchestration"}, session=session)
    result = run_adapter("track-skill", skill, home, env=default_state_env(home))
    assert (result.returncode, result.stdout, result.stderr) == (0, b"", b"")
    [directory] = {path for path in candidates if (path / f"{session}.jsonl").is_file()}
    info = directory.stat()
    assert info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700
    assert not (home / ".claude").exists()
  finally:
    for path in candidates:
      (path / f"{session}.jsonl").unlink(missing_ok=True)


@pytest.mark.parametrize("role", GATED_ROLES + [GENERIC_TOOL])
def test_the_gate_denies_when_the_state_dir_cannot_be_created(role: str, home: Path, broken_state: Path) -> None:
  result = gate(home, role, env=make_env(home, broken_state))
  assert (result.returncode, result.stdout) == (2, b"")
  assert str(broken_state) in result.stderr.decode()


@pytest.mark.parametrize("role", EXEMPT_ROLES)
def test_roles_exempt_from_the_doctrine_still_pass_when_the_state_dir_cannot_be_created(
  role: str, home: Path, broken_state: Path
) -> None:
  assert outcome(gate(home, role, env=make_env(home, broken_state), prompt="look this up")) == "allow"


def test_the_kill_switch_lifts_the_denial_but_not_the_slice_gate(home: Path, broken_state: Path) -> None:
  env = {**make_env(home, broken_state), GATE_OFF_ENV: "0"}
  assert outcome(gate(home, "planner", env=env)) == "allow"
  no_slice = gate(home, "code-writer-t2", env=env, prompt="no slice line here")
  assert outcome(no_slice) == "deny" and "slice_id" in reason(no_slice)


def test_loading_the_skill_is_not_blocked_when_the_state_dir_cannot_be_created(home: Path, broken_state: Path) -> None:
  skill = pre("skill", {"name": "task-orchestration"})
  result = run_adapter("track-skill", skill, home, env=make_env(home, broken_state))
  assert (result.returncode, result.stdout) == (0, b"")
  assert str(broken_state) in result.stderr.decode()


def test_a_state_dir_open_to_others_is_not_trusted(home: Path) -> None:
  state_dir(home).mkdir()
  state_dir(home).chmod(0o755)
  result = gate(home, "planner")
  assert (result.returncode, result.stdout) == (2, b"")
  assert str(state_dir(home)) in result.stderr.decode()


def test_a_state_dir_that_is_a_symlink_is_not_followed(home: Path, tmp_path: Path) -> None:
  elsewhere = tmp_path / "elsewhere"
  elsewhere.mkdir(mode=0o700)
  state_dir(home).symlink_to(elsewhere)
  assert gate(home, "planner").returncode == 2
  load_doctrine(home)  # not blocked, and writes nothing through the link
  assert list(elsewhere.iterdir()) == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_an_unreadable_state_file_denies_instead_of_letting_the_hook_fail_open(home: Path) -> None:
  load_doctrine(home)
  (state_dir(home) / "sess-1.jsonl").chmod(0)
  result = gate(home, "planner")
  assert (result.returncode, result.stdout) == (2, b"")
  assert "sess-1.jsonl" in result.stderr.decode()


def test_a_state_dir_of_another_user_is_not_trusted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  # Through the CLI this needs a second account, so only the uid is faked; a root process would trust the dir otherwise.
  adapter = load_hook("dsh_adapter.py")
  directory = tmp_path / "state"
  adapter.private_dir(directory)
  stranger = os.getuid() + 1
  monkeypatch.setattr(adapter.os, "getuid", lambda: stranger)
  with pytest.raises(PermissionError, match="private directory"):
    adapter.private_dir(directory)


# DSH confines a hook with a Seatbelt profile: workspace-write mode denies every file write except under the project
# root, /tmp and the temp dir, read-only mode opens nothing (dsh-sandbox-local, dsh-sandbox writableRoots). The same
# profiles, applied here. They need a working sandbox-exec, which a process that is already sandboxed does not get.
SANDBOX_EXEC = shutil.which("sandbox-exec")


def seatbelt_usable() -> bool:
  if not SANDBOX_EXEC:
    return False
  probe = subprocess.run([SANDBOX_EXEC, "-p", "(version 1) (allow default)", "true"], capture_output=True)
  return probe.returncode == 0


needs_seatbelt = pytest.mark.skipif(not seatbelt_usable(), reason="sandbox-exec is missing or refuses to run")


def confine(*writable: Path) -> list[str]:
  """argv prefix that denies every file write but /dev/null and `writable`, spelled canonically as DSH grants them."""
  assert SANDBOX_EXEC
  grants = "".join(f' (subpath "{os.path.realpath(root)}")' for root in writable)
  profile = f'(version 1) (allow default) (deny file-write*) (allow file-write* (literal "/dev/null"){grants})'
  return [SANDBOX_EXEC, "-p", profile]


def run_confined(sandbox: list[str], target: str, payload: Payload, env: dict[str, str], cwd: Path) -> Result:
  argv = [*sandbox, sys.executable, str(ADAPTER), target]
  stdin = json.dumps(payload).encode("utf-8")
  return subprocess.run(argv, input=stdin, capture_output=True, env=env, cwd=cwd, timeout=60)


@needs_seatbelt
@pytest.mark.parametrize("open_dir", ["/tmp", "temp-dir"])
def test_the_gate_decides_under_the_dsh_hook_sandbox(open_dir: str, home: Path, tmp_path: Path) -> None:
  """HOME, hence ~/.claude/logs, is closed; the state must land in the one dir the profile opens besides the project."""
  workspace, temp_dir = tmp_path / "workspace", tmp_path / "temp-dir"
  workspace.mkdir()
  temp_dir.mkdir()
  name = f"dsh-claude-state-{os.getuid()}"
  env = default_state_env(home)
  if open_dir == "/tmp":
    sandbox, state = confine(workspace, Path("/tmp")), Path("/tmp") / name
  else:  # /tmp closed: the fallback is the temp dir of the process
    sandbox, state = confine(workspace, temp_dir), temp_dir / name
    env["TMPDIR"] = str(temp_dir)
  if subprocess.run([*sandbox, "mkdir", str(home / ".claude")], capture_output=True).returncode == 0:
    pytest.skip("the profile leaves HOME writable: the test tmp dir lies under /tmp")
  session = f"seatbelt-{uuid.uuid4().hex}"
  try:
    denied = run_confined(sandbox, "require-orchestration", role_call("planner", session=session), env, workspace)
    assert outcome(denied) == "deny" and "task-orchestration" in reason(denied)
    skill = pre("skill", {"name": "task-orchestration"}, session=session)
    loaded = run_confined(sandbox, "track-skill", skill, env, workspace)
    assert (loaded.returncode, loaded.stdout, loaded.stderr) == (0, b"", b"")
    allowed = run_confined(sandbox, "require-orchestration", role_call("planner", session=session), env, workspace)
    assert outcome(allowed) == "allow"
    assert (state / f"{session}.jsonl").is_file()
  finally:
    (state / f"{session}.jsonl").unlink(missing_ok=True)


@needs_seatbelt
def test_a_gated_role_is_denied_in_the_read_only_hook_sandbox_and_an_exempt_one_passes(
  home: Path, tmp_path: Path
) -> None:
  """Read-only mode opens no dir at all, not even a temp dir: the gate cannot keep state, and says where."""
  sandbox, env, session = confine(), default_state_env(home), f"seatbelt-{uuid.uuid4().hex}"
  gated = run_confined(sandbox, "require-orchestration", role_call("planner", session=session), env, tmp_path)
  assert (gated.returncode, gated.stdout) == (2, b"")
  assert f"/tmp/dsh-claude-state-{os.getuid()}" in gated.stderr.decode()
  exempt = role_call("reviewer", prompt="look this up", session=session)
  assert outcome(run_confined(sandbox, "require-orchestration", exempt, env, tmp_path)) == "allow"
  report = post("planner", {"prompt": "x"}, VALID_REPORT, session=session)
  logged_run = run_confined(sandbox, "log-run", report, env, tmp_path)  # nowhere to write: it must not block
  assert (logged_run.returncode, logged_run.stdout) == (0, b"")


# --- the role-less `subagent` tool: gated like a Claude Agent call with no subagent_type ---

CLAUDE_DOCTRINE_LOAD = json.dumps({
  "type": "assistant",
  "message": {"role": "assistant", "content": [
    {"type": "tool_use", "name": "Skill", "input": {"skill": "task-orchestration"}},
  ]},
}) + "\n"


def claude_agent_call(tool_input: Payload, transcript: Path, home: Path) -> Result:
  """A Claude `Agent` call straight into the hook, no adapter: the reference for `subagent`."""
  payload = {**pre("Agent", tool_input), "transcript_path": str(transcript)}
  return run_claude_hook("require-orchestration", payload, home)


@pytest.mark.parametrize("loaded,expected", [(False, "deny"), (True, "allow")])
def test_the_generic_subagent_tool_is_gated_like_an_untyped_claude_agent_call(
  loaded: bool, expected: str, home: Path, tmp_path: Path
) -> None:
  # No slice_id line: the Claude gate asks for one only from the slice roles, and general-purpose is not one.
  tool_input = {"description": "work", "prompt": "no slice line here"}
  transcript = tmp_path / "claude-transcript.jsonl"
  transcript.write_text(CLAUDE_DOCTRINE_LOAD if loaded else "")
  if loaded:
    load_doctrine(home)
  adapted = run_adapter("require-orchestration", pre(GENERIC_TOOL, tool_input), home)
  reference = claude_agent_call(tool_input, transcript, home)
  assert outcome(adapted) == expected, adapted.stdout
  assert adapted.returncode == reference.returncode
  assert adapted.stdout == reference.stdout
  assert adapted.stderr == reference.stderr


def test_the_generic_subagent_denial_names_general_purpose_and_the_doctrine(home: Path) -> None:
  text = reason(gate(home, GENERIC_TOOL))
  assert "subagent_type=general-purpose" in text and "task-orchestration" in text


@pytest.mark.parametrize("claimed", ["Explore", "reviewer", "web-researcher"])  # agent types the gate lets through
def test_a_subagent_type_in_the_input_cannot_pick_an_exempt_agent(claimed: str, home: Path, tmp_path: Path) -> None:
  tool_input = {"description": "work", "prompt": "look this up", "subagent_type": claimed}
  transcript = tmp_path / "claude-transcript.jsonl"
  transcript.write_text("")
  assert outcome(claude_agent_call(tool_input, transcript, home)) == "allow"  # exempt when Claude gets the type
  result = run_adapter("require-orchestration", pre(GENERIC_TOOL, tool_input), home)
  assert outcome(result) == "deny" and "subagent_type=general-purpose" in reason(result)


# --- failure policy ---

BAD_STDIN = [b"", b"not json", b"[]", b'"text"']


@pytest.mark.parametrize("stdin", BAD_STDIN)
@pytest.mark.parametrize("target", CLOSED_TARGETS)
def test_security_guards_fail_closed_on_a_bad_payload(target: str, stdin: bytes, home: Path) -> None:
  result = run_adapter(target, stdin, home)
  assert result.returncode == 2 and result.stdout == b""
  assert b"dsh_adapter" in result.stderr and target.encode() in result.stderr


@pytest.mark.parametrize("stdin", BAD_STDIN)
@pytest.mark.parametrize("target", OPEN_TARGETS)
def test_the_other_targets_fail_open_with_a_note(target: str, stdin: bytes, home: Path) -> None:
  result = run_adapter(target, stdin, home)
  assert result.returncode == 0 and result.stdout == b""
  assert b"dsh_adapter" in result.stderr and target.encode() in result.stderr


@pytest.mark.parametrize("target", ["", "nope", "trino_guardrail"])
def test_an_unknown_or_missing_target_fails_closed(target: str, home: Path) -> None:
  result = run_adapter(target, READ_CALL, home)
  assert result.returncode == 2 and b"dsh_adapter" in result.stderr


@pytest.mark.parametrize("target", SCRIPT_TARGETS)
def test_a_missing_hook_script_follows_the_policy(target: str, stub_hooks: Path, home: Path) -> None:
  # python itself exits 2 for a missing file: that must not turn an open guard into a block
  result = run_stub(stub_hooks, target, READ_CALL, home)
  assert result.returncode == (2 if target in CLOSED_TARGETS else 0)
  assert b"not found" in result.stderr and result.stdout == b""


@pytest.mark.parametrize("target", SCRIPT_TARGETS)
@pytest.mark.parametrize("crash", [
  "raise RuntimeError('boom')", "import sys; sys.exit(1)", "import os; os.kill(os.getpid(), 9)",
])
def test_a_crashing_hook_script_follows_the_policy(target: str, crash: str, stub_hooks: Path, home: Path) -> None:
  put_hook(stub_hooks, target, crash + "\n")
  result = run_stub(stub_hooks, target, READ_CALL, home)
  assert result.returncode == (2 if target in CLOSED_TARGETS else 0)
  assert b"dsh_adapter" in result.stderr and result.stdout == b""


@pytest.mark.parametrize("target", SCRIPT_TARGETS)
@pytest.mark.parametrize("code", [0, 2])
def test_stdout_stderr_and_exit_code_of_the_hook_pass_through(
  target: str, code: int, stub_hooks: Path, home: Path
) -> None:
  put_hook(stub_hooks, target, (
    "import sys\n"
    "sys.stdout.buffer.write('{\"ok\": \"привет\"}'.encode())\n"
    "sys.stderr.buffer.write('причина'.encode())\n"
    f"sys.exit({code})\n"
  ))
  result = run_stub(stub_hooks, target, READ_CALL, home)
  assert result.returncode == code
  assert result.stdout == '{"ok": "привет"}'.encode()
  assert result.stderr == "причина".encode()


# --- log-run ---

VALID_REPORT = "### Result\n`DONE` - SECRET-REPORT-TEXT\n\n### Changes\n- a.py\n\n### Verification\n- tests pass\n"
REVIEWER_REPORT = "### Verdict\n`APPROVE`\n\n### Findings\n- none\n\n### Final assessment\nfine\n"
STATUS_CASES = [
  ("code-writer-t2", VALID_REPORT, "valid"),
  ("reviewer", REVIEWER_REPORT, "valid"),
  ("finding-verifier", "Verdict: HOLDS - true.\nChecked: a.py:1\nResult: confirmed\n", "valid"),
  ("code-writer-t2", "I did it, trust me.", "malformed"),
  ("reviewer", "### Verdict\n`APPROVE`\n", "malformed"),
  ("code-writer-t2", "", "empty"),
  ("code-writer-t2", "  \n\t\n", "empty"),
  ("code-writer-t2", "Error: blocked by PreToolUse hook", "error"),
]


def runs_log(home: Path) -> Path:
  return home / ".claude" / "logs" / "dsh-subagent-runs.jsonl"


def logged(home: Path) -> list[Payload]:
  return [json.loads(line) for line in runs_log(home).read_text().splitlines()]


def test_log_run_writes_one_metadata_line_and_no_text(home: Path) -> None:
  tool_input = {"description": "port hooks", "prompt": "slice_id: dsh-hooks\nSECRET-PROMPT-TEXT"}
  result = run_adapter("log-run", post("code-writer-t2", tool_input, VALID_REPORT), home)
  assert (result.returncode, result.stdout, result.stderr) == (0, b"", b"")
  assert not state_dir(home).exists()  # the state dir holds the log only as the fallback
  [entry] = logged(home)
  stamp = datetime.fromisoformat(entry.pop("ts"))
  assert stamp.utcoffset() == timedelta(0)
  assert abs(datetime.now(timezone.utc) - stamp) < timedelta(minutes=1)
  assert entry == {
    "harness": "dsh", "session_id": "sess-1", "role": "code-writer-t2", "slice_id": "dsh-hooks",
    "description": "port hooks", "background": False, "report_status": "valid",
    "report_chars": len(VALID_REPORT),
  }
  text = runs_log(home).read_text()
  assert "SECRET-REPORT-TEXT" not in text and "SECRET-PROMPT-TEXT" not in text


@pytest.mark.parametrize("role,report,status", STATUS_CASES)
def test_log_run_classifies_the_report_by_the_role_contract(role: str, report: str, status: str, home: Path) -> None:
  run_adapter("log-run", post(role, {"description": "d", "prompt": "slice_id: s"}, report), home)
  [entry] = logged(home)
  assert (entry["report_status"], entry["report_chars"], entry["background"]) == (status, len(report), False)


@pytest.mark.parametrize("extra,response", [
  ({"run_in_background": True}, "whatever the harness returned"),
  ({}, "started subagent sa-17"),
  ({"run_in_background": False}, "started background subagent job job-3"),
])
def test_log_run_does_not_judge_a_background_ack(extra: Payload, response: str, home: Path) -> None:
  run_adapter("log-run", post("planner", {"description": "d", "prompt": "x", **extra}, response), home)
  [entry] = logged(home)
  assert entry["report_status"] == "background-not-applicable"
  assert entry["background"] is True and entry["report_chars"] is None


@pytest.mark.parametrize("extra", [{"run_in_background": True}, {}])
def test_log_run_a_denied_call_is_an_error_not_a_background_start(extra: Payload, home: Path) -> None:
  denied = "Error: Делегирование в subagent_type=planner без загруженной доктрины."
  run_adapter("log-run", post("planner", {"description": "d", "prompt": "x", **extra}, denied), home)
  [entry] = logged(home)
  assert (entry["report_status"], entry["background"], entry["report_chars"]) == ("error", False, len(denied))


@pytest.mark.parametrize("prompt,slice_id", [
  ("slice_id: dsh-hooks\nbody", "dsh-hooks"),
  ("- `slice_id`: a.b_c-1\nbody", "a.b_c-1"),
  ("we discuss the slice_id: not-a-declaration", None),
  ("", None),
])
def test_log_run_reads_the_slice_from_the_brief_like_the_gate(prompt: str, slice_id: str | None, home: Path) -> None:
  run_adapter("log-run", post("planner", {"description": "d", "prompt": prompt}, VALID_REPORT), home)
  assert logged(home)[0]["slice_id"] == slice_id


def test_log_run_appends(home: Path) -> None:
  for session in ("a", "b"):
    run_adapter("log-run", post("test-runner", {"prompt": "slice_id: s"}, VALID_REPORT, session=session), home)
  assert [entry["session_id"] for entry in logged(home)] == ["a", "b"]


def test_log_run_falls_back_to_the_state_dir_when_the_logs_dir_is_unwritable(home: Path, readonly_logs: Path) -> None:
  tool_input = {"description": "port hooks", "prompt": "slice_id: dsh-hooks\nbody"}
  result = run_adapter("log-run", post("code-writer-t2", tool_input, VALID_REPORT), home)
  fallback = state_dir(home) / "dsh-subagent-runs.jsonl"
  assert (result.returncode, result.stdout) == (0, b"")
  assert result.stderr.count(b"\n") == 1 and str(fallback) in result.stderr.decode()  # one note, naming the file
  [entry] = [json.loads(line) for line in fallback.read_text().splitlines()]
  assert (entry["role"], entry["slice_id"], entry["report_status"]) == ("code-writer-t2", "dsh-hooks", "valid")
  assert list(readonly_logs.iterdir()) == []


def test_log_run_never_blocks_when_it_cannot_write_anywhere(broken_home: Path, broken_state: Path) -> None:
  payload = post("planner", {"prompt": "x"}, VALID_REPORT)
  result = run_adapter("log-run", payload, broken_home, env=make_env(broken_home, broken_state))
  assert (result.returncode, result.stdout) == (0, b"")
  assert b"dsh_adapter" in result.stderr and str(broken_state) in result.stderr.decode()


# --- dsh-hooks.json ---

LITERAL = re.compile(r"[A-Za-z0-9_|]+")
COMMAND = re.compile(
  r"python3 (?P<script>/Users/USER/\.claude/hooks/dsh_adapter\.py) (?P<target>[a-z-]+)(?P<closed> \|\| exit 2)?"
)


def wired() -> dict[str, list[Payload]]:
  hooks: dict[str, list[Payload]] = json.loads(CONFIG.read_text())["hooks"]
  return hooks


def command_parts(command: str) -> re.Match[str]:
  parts = COMMAND.fullmatch(command)
  assert parts, command
  return parts


def matches(matcher: str, tool: str) -> bool:
  """DSH's matchesMatcher, Claude dialect: word-and-pipe patterns are exact alternatives, the rest is an unanchored regex."""
  if matcher in ("", "*"):
    return True
  if LITERAL.fullmatch(matcher):
    return tool in matcher.split("|")
  return re.search(matcher, tool) is not None


def targets_for(event: str, tool: str) -> list[str]:
  found: list[str] = []
  for group in wired().get(event, []):
    if matches(group.get("matcher", ""), tool):
      found += [command_parts(hook["command"])["target"] for hook in group["hooks"]]
  return found


def test_only_events_dsh_supports_are_wired() -> None:
  assert set(wired()) == {"PreToolUse", "PostToolUse"}


def test_every_matcher_compiles() -> None:
  for groups in wired().values():
    for group in groups:
      assert isinstance(group["matcher"], str) and group["matcher"]
      if not LITERAL.fullmatch(group["matcher"]):
        re.compile(group["matcher"])


def test_every_command_runs_a_known_target_through_an_existing_adapter() -> None:
  for event, groups in wired().items():
    for group in groups:
      for hook in group["hooks"]:
        parts = command_parts(hook["command"])
        assert hook["type"] == "command"
        # The config holds a placeholder home path, so check the adapter file in HOOKS.
        assert (HOOKS / Path(parts["script"]).name).is_file()
        assert parts["target"] in SCRIPT_TARGETS + ["track-skill", "log-run"]
        assert hook["timeout"] == (30 if parts["target"] == "log-run" else 10)
        # the shell-level fallback covers what the adapter cannot catch itself (SyntaxError, no python3)
        assert bool(parts["closed"]) == (parts["target"] in CLOSED_TARGETS), (event, hook["command"])


@pytest.mark.parametrize("role", ROLES)
def test_each_role_is_gated_before_and_logged_after(role: str) -> None:
  assert targets_for("PreToolUse", role) == ["require-orchestration"]
  assert targets_for("PostToolUse", role) == ["log-run"]


@pytest.mark.parametrize("tool", [
  "code-writer-t22", "code-writer-t", "xreviewer", "reviewer2", "planner-extra", "Explore",
  "task", "reviewer-bash-guard", "",
  # the other DSH delegation tools and near-misses of `subagent`: only the exact name is gated
  "subagent_fork", "subagent_codex", "subagent_claude_code", "subagents", "xsubagent", "Subagent",
])
def test_the_role_matcher_is_anchored(tool: str) -> None:
  assert targets_for("PreToolUse", tool) == [] and targets_for("PostToolUse", tool) == []


def test_the_generic_subagent_tool_is_gated_but_not_logged() -> None:
  assert targets_for("PreToolUse", GENERIC_TOOL) == ["require-orchestration"]
  assert targets_for("PostToolUse", GENERIC_TOOL) == []  # log-run has no report contract for a role-less call


def test_the_roles_are_exactly_the_ones_with_a_report_contract() -> None:
  assert sorted(ROLES) == sorted(load_hook("log-subagent.py").ROLE_CONTRACTS)


@pytest.mark.parametrize("tool,expected", [
  ("mcp__trino__execute_query", ["trino-guardrail"]),
  ("mcp__trino__explain_query", ["trino-guardrail"]),
  ("read", ["check-file-size"]), ("edit", ["config-protection"]), ("write", ["config-protection"]),
  ("skill", ["track-skill"]),
  ("mcp__trino__list_tables", []), ("mcp__trino__execute_queryx", []), ("read_file", []),
  ("multi_edit", []), ("write_file", []), ("skills", []), ("bash", []), ("grep", []),
  ("Read", []), ("Edit", []),
])
def test_literal_matchers_select_exactly_their_tools(tool: str, expected: list[str]) -> None:
  assert targets_for("PreToolUse", tool) == expected
  assert targets_for("PostToolUse", tool) == []
