#!/usr/bin/env python3
"""PreToolUse gate for the exit of a code-writer slice.

The runs log showed that 37% of code-writer slices never got a `test-runner`
run and 35% of sessions never ran `reviewer`. The main agent ran the tests
itself and committed. Other hooks gate the entry of a slice. This hook gates
the exit.

An open slice is a code-writer slice of this session with no `test-runner`
run after its last implementer run. The runs log is the source of truth
(`~/.claude/logs/subagent-runs.jsonl`, rows with schema 4 or higher).

The hook denies three actions:
1. `git commit`, `git push`, `gh pr create`, `gh pr merge` when a slice is not
   validated by `test-runner` or not reviewed by `reviewer`.
2. A `code-writer*` Agent call for a different slice when a slice is not
   validated. A retry of the same slice passes.
3. `mcp__ccd_turn__wrap_up` when a slice is not validated. A recap that
   starts with PARTIAL or BLOCKED passes.

When the hook does not deny, it prints nothing. It never prints `allow`,
because an `allow` would skip the permission prompts of `git push`.

The hook fails open on every uncertainty: missing log, bad JSON, any
exception. It is a discipline guardrail, not a security boundary.

Known limits. The hook sees a closing command only at the start of a shell
segment. It does not see `command git push`, `env FOO=1 git push`,
`{ git commit; }` or `if git commit`. A heredoc body line that starts with
`git push` or `git commit` is denied. Write such files with the Write tool.

Disable with CLAUDE_EXIT_GATE=0.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

IMPLEMENTERS = {"code-writer-t1", "code-writer-t2", "code-writer-t3", "code-writer"}

# One shell segment that starts with a closing command. The global options of
# git (`-C path`, `-c k=v`, `--no-pager`) may stand before the subcommand.
GIT_RE = re.compile(
    r'''^[\s(]*(?:\w+=\S*\s+)*git(?:\s+(?:-[Cc]\s+(?:"[^"]*"|'[^']*'|[^\s"']\S*)|--?\w[\w-]*(?:=\S+)?))*\s+(?:commit|push)(?:\s|$)'''
)
GH_RE = re.compile(
    r"^[\s(]*(?:\w+=\S*\s+)*gh(?:\s+(?:-R|--repo)(?:\s+|=)\S+)*\s+pr\s+(?:create|merge)(?:\s|$)"
)
SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||[;|\n]")
PARTIAL_RE = re.compile(r"^\s*(?:PARTIAL|BLOCKED)\b")


def runs_log_path() -> Path:
    return Path(os.environ.get("CLAUDE_SUBAGENT_LOG") or Path.home() / ".claude" / "logs" / "subagent-runs.jsonl")


def own_log_path() -> Path:
    return Path(os.environ.get("CLAUDE_EXIT_GATE_LOG") or Path.home() / ".claude" / "logs" / "orchestration-exit-gate.jsonl")


def log_event(tool_name: str | None, decision: str, reason: str) -> None:
    try:
        path = own_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tool_name": tool_name,
                "decision": decision,
                "reason": reason,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass  # logging must never be the reason a tool call fails


def deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))
    sys.exit(0)


def open_slices(session_id: str) -> dict[str, dict[str, bool]]:
    """Map slice_id -> {"validated": bool, "reviewed": bool} for this session."""
    log = runs_log_path()
    if not log.is_file():
        return {}
    last: dict[str, str] = {}  # slice_id -> max implementer timestamp
    broken: set[str] = set()  # slices with a timestamp that cannot be compared
    runners: dict[str, list[str]] = {}  # slice_id -> test-runner timestamps
    reviews: list[str] = []
    with log.open() as f:
        for line in f:
            if session_id not in line:  # cheap prefilter
                continue
            try:
                row = json.loads(line)
                if row["session_id"] != session_id or row["schema"] < 4:
                    continue
                agent, slice_id, ts = row.get("agent_type"), row.get("slice_id"), row.get("timestamp")
                if agent in IMPLEMENTERS and slice_id:
                    if not isinstance(ts, str):
                        broken.add(slice_id)
                    elif ts > last.get(slice_id, ""):
                        last[slice_id] = ts
                elif agent == "test-runner" and slice_id and isinstance(ts, str):
                    runners.setdefault(slice_id, []).append(ts)
                elif agent == "reviewer" and isinstance(ts, str):
                    reviews.append(ts)
            except Exception:
                continue  # one malformed line must not fail the whole computation
    slices = {}
    for slice_id, t_last in last.items():
        if slice_id in broken:
            continue
        slices[slice_id] = {
            "validated": any(ts >= t_last for ts in runners.get(slice_id, [])),
            "reviewed": any(ts >= t_last for ts in reviews),
        }
    return slices


def is_closing_command(command: str) -> bool:
    return any(
        GIT_RE.match(segment) or GH_RE.match(segment)
        for segment in SEGMENT_SPLIT_RE.split(command)
    )


def missing_text(slices: dict[str, dict[str, bool]], names: list[str], need_review: bool) -> str:
    parts = []
    for name in names:
        lacks = []
        if not slices[name]["validated"]:
            lacks.append(f"test-runner (запусти test-runner с slice_id: {name})")
        if need_review and not slices[name]["reviewed"]:
            lacks.append("reviewer (запусти reviewer)")
        parts.append(f"{name}: нет {' и '.join(lacks)}")
    return "; ".join(parts)


def decide(data: dict[str, Any]) -> tuple[str, str] | None:
    """Return (tool_name, reason) to deny, or None to pass silently."""
    tool_name = data.get("tool_name")
    tool_input = data.get("tool_input") or {}
    session_id = data.get("session_id")
    if not session_id:
        return None

    if tool_name == "Bash":
        if not is_closing_command(str(tool_input.get("command") or "")):
            return None
        need_review = True
        current = None
    elif tool_name == "Agent":
        if not str(tool_input.get("subagent_type") or "").startswith("code-writer"):
            return None
        need_review = False
        match = BRIEF_RE["slice_id"].search(str(tool_input.get("prompt") or ""))
        current = match.group(1) if match else None
    elif tool_name == "mcp__ccd_turn__wrap_up":
        if PARTIAL_RE.search(str(tool_input.get("recap") or "")):
            return None
        need_review = False
        current = None
    else:
        return None

    slices = open_slices(session_id)
    names = [
        name for name, state in slices.items()
        if name != current and (not state["validated"] or (need_review and not state["reviewed"]))
    ]
    if not names:
        return None
    reason = (
        "Слайс code-writer не закрыт: "
        + missing_text(slices, names, need_review)
        + ". Сначала сделай это и повтори вызов. Отключить гейт: CLAUDE_EXIT_GATE=0."
    )
    return tool_name, reason


# Imported after log_event exists, so a missing brief_fields.py fails open
# like any other exception: logged as allow-on-error, nothing printed.
sys.path.insert(0, str(Path(__file__).parent))
try:
    from brief_fields import BRIEF_RE  # noqa: E402
except Exception as e:
    log_event(None, "allow-on-error", repr(e))
    sys.exit(0)


def main() -> None:
    if os.environ.get("CLAUDE_EXIT_GATE") == "0":
        return
    try:
        result = decide(json.load(sys.stdin))
    except Exception as e:
        log_event(None, "allow-on-error", repr(e))
        return
    if result:
        tool_name, reason = result
        log_event(tool_name, "deny", reason)
        deny(reason)


if __name__ == "__main__":
    main()
    sys.exit(0)
