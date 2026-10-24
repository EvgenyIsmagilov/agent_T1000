#!/usr/bin/env python3
"""Thin adapter that runs the Claude Code hooks inside DeepSeek Harness (DSH).

DSH loads one hooks config (dsh-hooks.json, Claude `hooks` shape) and calls
`python3 dsh_adapter.py <target>` with its own payload on stdin. The adapter
translates that payload into the Claude shape the existing hook expects, runs
the hook unchanged and hands its stdout, stderr and exit code back to DSH.
Hook logic is not copied here.

Targets:
  trino-guardrail, check-file-size, config-protection, require-orchestration
      the hook of the same name from this directory;
  track-skill   PreToolUse of `skill`: remember that the doctrine skill was loaded;
  log-run       PostToolUse of a role tool: one line in ~/.claude/logs/dsh-subagent-runs.jsonl,
                or in the state dir when that is not writable (metadata only, never the
                report text).
reviewer-bash-guard is not ported: DSH tool events do not say which role called.

Doctrine state. require-orchestration decides from a session transcript, and DSH
never provides one (transcript_path is always ""). The adapter keeps a synthetic
transcript per session, <state dir>/<session_id>.jsonl: track-skill appends the line
skill_loaded() recognizes when `task-orchestration` is loaded, and every role-tool call
is checked against that file, created empty when missing so a missing load DENIES
instead of failing open. The role-less `subagent` tool is checked the same way, as the
Agent call with no subagent_type that Claude runs as `general-purpose`; a subagent_type
in its input is overwritten, so it cannot name an exempt agent. It is gated but not
logged: log-run judges a report by its role's contract, and a role-less call has none.
A child agent has its own session_id, hence its own empty file: it must load the skill
itself to delegate to gated roles.

State dir. DSH runs a hook in its workspace-write sandbox, which lets it write only under
the project root, /tmp and the temp dir, so state kept in ~/.claude/logs fails exactly
where the gate needs it. The dir is /tmp/dsh-claude-state-<uid>, mode 700: /tmp rather than
the temp dir because on macOS TMPDIR differs per process, the uid because /tmp is shared.
Only when /tmp refuses the write does the temp dir's twin of it take over. A dir that is a
link, someone else's or open to others is refused: its owner could read or forge the state.
DSH_ADAPTER_STATE_DIR replaces the whole choice (the tests use it). A cleaned /tmp costs one
reload of the skill: the file comes back empty and the gate denies until track-skill sees it.
If no state file can be created or read, the gate cannot see the doctrine, so it DENIES
(exit 2, the reason names the paths) rather than fail open, except where the hook never reads
the state: an exempt role (the hook's own list) or CLAUDE_ORCHESTRATION_GATE=0.

Failure policy. DSH treats every exit code except 2 as "the hook failed, go on", so
a crash would silently switch a guard off. Any adapter failure (bad JSON, unmapped
tool, missing/crashing/hanging hook script, unknown target) therefore exits 2 for
the security guards (trino-guardrail, config-protection) and exits 0 with a note on
stderr for the rest. log-run never blocks; track-skill never blocks the skill load.

Written to run on the python3 DSH may find on its PATH, including the macOS 3.9:
annotations stay lazy and log-subagent.py is loaded with lazy annotations too.
"""
from __future__ import annotations

import __future__
import errno
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator

Payload = dict[str, Any]  # JSON харнесса: схема не фиксирована

HOOKS_DIR = Path(__file__).resolve().parent
SKILL_NAME = "task-orchestration"
BLOCK = 2  # код выхода, которым DSH блокирует вызов
STATE_DIR_ENV = "DSH_ADAPTER_STATE_DIR"  # заменяет каталог состояния целиком: тесты не трогают /tmp
GATE_OFF_ENV = "CLAUDE_ORCHESTRATION_GATE"  # выключатель самого хука: при 0 он не читает транскрипт
RUNS_LOG = "dsh-subagent-runs.jsonl"

HOOK_TARGETS = ("trino-guardrail", "check-file-size", "config-protection", "require-orchestration")
OWN_TARGETS = ("track-skill", "log-run")
FAIL_OPEN = ("check-file-size", "require-orchestration", "track-skill", "log-run")

# Таймаут хука в DSH 10 с, а убитый по таймауту хук DSH считает «не решил»: уложиться раньше,
# чтобы сбой охранника стал отказом, а не молчаливым пропуском.
HOOK_TIMEOUT_S = 8

TOOL_MAP = {
  "read": "Read", "write": "Write", "edit": "Edit", "bash": "Bash", "grep": "Grep",
  "glob": "Glob", "skill": "Skill", "web_fetch": "WebFetch", "web_search": "WebSearch",
}
# `subagent` — инструмент DSH без роли: Claude исполняет Agent без subagent_type как general-purpose.
SUBAGENT_TYPES = {"subagent": "general-purpose"}
# Ответ фонового вызова роли: `started subagent <id>` или `started background subagent job <id>`.
ACK_RE = re.compile(r"\s*started (?:background )?subagent\b", re.IGNORECASE)
# Отказ хука или сбой вызова: PostToolUse приходит и для них, и такой вызов не стартовал, даже фоновый.
ERROR_PREFIX = "Error:"


class StateError(Exception):
  """No state file can be created or opened, so the doctrine gate cannot tell whether the skill was loaded."""


def logs_dir() -> Path:
  return Path.home() / ".claude" / "logs"


def note(message: str) -> None:
  sys.stderr.buffer.write(f"dsh_adapter: {message}\n".encode("utf-8", errors="replace"))


def append_line(path: Path, record: Payload) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  with path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_hook_module(filename: str) -> ModuleType:
  """Load a hyphenated hook file as a module, so its table and validator are reused, not copied."""
  path = HOOKS_DIR / filename
  module = ModuleType(path.stem.replace("-", "_"))
  module.__file__ = str(path)
  # Ленивые аннотации: `str | None` в сигнатурах log-subagent.py на python 3.9 падает при импорте.
  source = compile(
    path.read_text(encoding="utf-8"), str(path), "exec",
    flags=__future__.annotations.compiler_flag, dont_inherit=True,
  )
  exec(source, module.__dict__)
  return module


def temp_base() -> str:
  try:
    return tempfile.gettempdir()
  except OSError:  # нет ни одного каталога для записи (песочница read-only): /tmp сам вернёт понятную ошибку
    return "/tmp"


def state_dir_candidates() -> Iterator[Path]:
  """State dirs, best first: /tmp is the same for every process, TMPDIR on macOS is not."""
  override = os.environ.get(STATE_DIR_ENV)
  if override:
    yield Path(override)
    return
  name = f"dsh-claude-state-{os.getuid()}"
  yield Path("/tmp") / name
  base = temp_base()  # лишь когда /tmp не подошёл: gettempdir() пробует писать в каталог
  if base != "/tmp":
    yield Path(base) / name


def private_dir(path: Path) -> None:
  """Create `path` for this user alone; one that already exists must be a real dir of ours, shut to others."""
  path.mkdir(mode=0o700, exist_ok=True)
  info = path.lstat()
  if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
    raise PermissionError(errno.EACCES, "not a private directory of this user", str(path))


def state_file(name: str) -> Path:
  """`name` in the first state dir that works, created empty when missing; StateError names every failure."""
  failures: list[str] = []
  for directory in state_dir_candidates():
    try:
      private_dir(directory)
      path = directory / name
      os.close(os.open(path, os.O_RDWR | os.O_CREAT, 0o600))  # файл читается и пишется, иначе это отказ
      return path
    except OSError as error:
      failures.append(f"{error.filename or directory}: {error.strerror or error}")
  raise StateError("; ".join(failures))


def session_file_name(data: Payload) -> str:
  """The session's transcript name; only [A-Za-z0-9._-] survive in it, so no path escapes the state dir."""
  session = re.sub(r"[^A-Za-z0-9._-]", "_", str(data.get("session_id") or "")) or "no-session"
  return f"{session}.jsonl"


def gate_reads_state(role: str) -> bool:
  """Whether require-orchestration reads the session state for this call; the exempt list is the hook's own."""
  if os.environ.get(GATE_OFF_ENV) == "0":
    return False
  return role not in load_hook_module("require-orchestration.py").EXEMPT_AGENTS


def claude_tool_name(name: str) -> str:
  if name in TOOL_MAP:
    return TOOL_MAP[name]
  if name.startswith("mcp__"):
    return name
  # Неизвестное имя дошло бы до хука как есть, и он молча разрешил бы вызов.
  raise ValueError(f"no Claude equivalent for DSH tool {name!r}")


def claude_payload(target: str, data: Payload) -> Payload:
  payload = dict(data)
  name = str(data.get("tool_name") or "")
  if target == "require-orchestration":
    # Роль-инструмент DSH — это вызов Agent с subagent_type; транскрипт — синтетический файл сессии.
    # subagent_type из входа вызова затирается: иначе `subagent_type: Explore` в нём обошёл бы гейт.
    payload["tool_name"] = "Agent"
    role = SUBAGENT_TYPES.get(name, name)
    payload["tool_input"] = {**(data.get("tool_input") or {}), "subagent_type": role}
    try:
      payload["transcript_path"] = str(state_file(session_file_name(data)))
    except StateError:
      if gate_reads_state(role):
        raise
      payload["transcript_path"] = ""  # хук решит и без него: отказывать нечему
  else:
    payload["tool_name"] = claude_tool_name(name)
  return payload


def last_line(raw: bytes) -> str:
  lines = raw.decode("utf-8", errors="replace").strip().splitlines()
  return lines[-1][:200] if lines else ""


def run_hook(target: str, payload: Payload) -> int:
  script = HOOKS_DIR / f"{target}.py"
  # Без проверки python сам вернул бы 2 («can't open file»): отсутствующий скрипт стал бы блокировкой.
  if not script.is_file():
    raise FileNotFoundError(f"hook script {script} not found")
  result = subprocess.run(
    [sys.executable, str(script)], input=json.dumps(payload).encode("utf-8"),
    capture_output=True, timeout=HOOK_TIMEOUT_S, check=False,
  )
  if result.returncode not in (0, BLOCK):
    raise RuntimeError(f"{script.name} exited with {result.returncode}: {last_line(result.stderr)}")
  sys.stdout.buffer.write(result.stdout)
  sys.stderr.buffer.write(result.stderr)
  return result.returncode


def track_skill(data: Payload) -> None:
  name = str((data.get("tool_input") or {}).get("name") or "")
  if name.split(":")[-1] != SKILL_NAME:  # как в skill_loaded(): плагинный префикс не мешает
    return
  call = {"type": "tool_use", "name": "Skill", "input": {"skill": name}}
  message = {"role": "assistant", "content": [call]}
  append_line(state_file(session_file_name(data)), {"type": "assistant", "message": message})


def report_status(role: str, text: str, background: bool) -> str:
  if text.startswith(ERROR_PREFIX):
    return "error"
  if background:
    return "background-not-applicable"
  _, presence, contract, _ = load_hook_module("log-subagent.py").validate_report(role, True, text)
  if contract == "exempt":
    raise ValueError(f"no report contract for role {role!r}")
  if presence != "present":
    return "empty"
  return "valid" if contract == "valid" else "malformed"


def log_run(data: Payload) -> None:
  tool_input = data.get("tool_input") or {}
  role = str(data.get("tool_name") or "")
  response = data.get("tool_response")
  text = response if isinstance(response, str) else ""
  background = not text.startswith(ERROR_PREFIX) and (
    bool(tool_input.get("run_in_background")) or ACK_RE.match(text) is not None
  )
  brief = str(tool_input.get("prompt") or tool_input.get("description") or "")
  slice_match = load_hook_module("brief_fields.py").BRIEF_RE["slice_id"].search(brief)
  record = {
    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "harness": "dsh",
    "session_id": data.get("session_id"),
    "role": role,
    "slice_id": slice_match.group(1) if slice_match else None,
    "description": tool_input.get("description"),
    "background": background,
    "report_status": report_status(role, text, background),
    "report_chars": None if background else len(text),
  }
  primary = logs_dir() / RUNS_LOG
  try:
    append_line(primary, record)
  except OSError as error:
    # ~/.claude/logs вне зоны записи песочницы DSH: строку не теряем, кладём рядом с состоянием
    fallback = state_file(RUNS_LOG)
    append_line(fallback, record)
    note(f"log-run: {primary} недоступен ({error.strerror or error}), строка записана в {fallback}")


def dispatch(target: str, data: Payload) -> int:
  if target == "track-skill":
    track_skill(data)
  elif target == "log-run":
    log_run(data)
  else:
    try:
      payload = claude_payload(target, data)
    except StateError as error:
      # Без файла состояния гейт не видит доктрину, а тихий пропуск отключил бы его: отказ с причиной.
      note(
        f"{target}: вызов заблокирован (fail closed): гейт не может создать файл состояния сессии "
        f"({error}) и не проверит, что навык {SKILL_NAME} загружен. Это сбой окружения: "
        "повтор вызова не поможет, сообщи пользователю."
      )
      return BLOCK
    return run_hook(target, payload)
  return 0


def fail(target: str, error: Exception) -> int:
  detail = f"{type(error).__name__}: {error}"
  if target in FAIL_OPEN:
    message, code = f"{target} пропущен (fail open): {detail}", 0
  else:
    message, code = f"{target} не отработал, вызов заблокирован (fail closed): {detail}", BLOCK
  note(message)
  return code


def main(argv: list[str]) -> int:
  target = argv[1] if len(argv) == 2 else ""
  try:
    known = HOOK_TARGETS + OWN_TARGETS
    if target not in known:
      raise ValueError(f"unknown target {target!r}; expected one of {', '.join(known)}")
    data = json.loads(sys.stdin.buffer.read())
    if not isinstance(data, dict):
      raise ValueError("payload is not a JSON object")
    return dispatch(target, data)
  except Exception as error:
    # Любой сбой идёт по политике цели: трейсбек дал бы exit 1, а его DSH не считает блокировкой.
    return fail(target, error)


if __name__ == "__main__":
  sys.exit(main(sys.argv))
