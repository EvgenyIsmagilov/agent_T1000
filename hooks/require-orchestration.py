#!/usr/bin/env python3
"""PreToolUse gate for the Agent tool: no delegation before the doctrine loads.

CLAUDE.md requires loading the `task-orchestration` skill before delegating to
subagents, but nothing enforced it — a prompt-level rule holds only while the
model happens to remember it. This blocks delegation until the skill is actually
loaded in the session — as a `Skill` tool call or as a user slash command — so
the first denial costs one turn and every later call passes untouched.

Read-only lookup agents are exempt: a search or a documentation question needs
a well-posed question, not the orchestration doctrine.

Fails open on every uncertainty — unreadable transcript, missing path, any
exception. This is a discipline guardrail, not a security boundary: it must
never be the reason delegation becomes impossible.

Disable with CLAUDE_ORCHESTRATION_GATE=0.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_NAME = "task-orchestration"
LOG_PATH = Path.home() / ".claude" / "logs" / "orchestration-gate.jsonl"

# A skill the user invokes as a slash command writes no `Skill` tool_use at
# all — only this marker, inside the string content of a `user` record.
COMMAND_NAME_RE = re.compile(
    r"<command-name>/?(?:[A-Za-z0-9_.-]+:)?" + re.escape(SKILL_NAME) + r"</command-name>"
)

# Agents whose work is a lookup, not a delegation of work: gating them only
# taxes the cheapest and most-encouraged calls. Everything else is gated,
# including agent types added later — a guardrail should fail closed on
# names it does not recognize. `Plan` stays gated on purpose: reaching for it
# means the work is already being orchestrated.
EXEMPT_AGENTS = {
    "Explore",
    "web-researcher-fast",
    "docs-researcher-deep",
    "claude-code-guide",
    "statusline-setup",
}


def log_event(agent_type, decision, reason):
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a") as f:
            f.write(json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent_type": agent_type,
                "decision": decision,
                "reason": reason,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass  # logging must never be the reason a delegation fails


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def allow():
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }))
    sys.exit(0)


def skill_loaded(path):
    """True when this session already loaded the skill, by either real path.

    Two forms count, and nothing else:

    * a `Skill` tool_use block whose `input.skill` names it — a plugin-qualified
      name (`plugin:task-orchestration`) is the same skill;
    * a `user` record whose string content carries the `<command-name>` marker,
      which is all a slash-command invocation leaves behind.

    Both are matched structurally rather than by the bare name: the words
    "task-orchestration" appear in plenty of ordinary prose, and a mention is
    not a load. The marker counts only on a `user` record, so an assistant
    writing that same marker into an explanation cannot open the gate.
    """
    with Path(path).open() as f:
        for line in f:
            # Cheap prefilter: transcripts run to megabytes and almost no line
            # carries either form.
            if '"Skill"' not in line and SKILL_NAME not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            message = rec.get("message")
            content = message.get("content") if isinstance(message, dict) else None

            if isinstance(content, str):
                if rec.get("type") == "user" and COMMAND_NAME_RE.search(content):
                    return True
                continue

            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("name") != "Skill":
                    continue
                skill = (block.get("input") or {}).get("skill") or ""
                if skill.split(":")[-1] == SKILL_NAME:
                    return True
    return False


def main():
    if os.environ.get("CLAUDE_ORCHESTRATION_GATE") == "0":
        allow()
        return

    data = json.load(sys.stdin)
    if data.get("tool_name") != "Agent":
        allow()
        return

    tool_input = data.get("tool_input", {}) or {}
    agent_type = tool_input.get("subagent_type") or "general-purpose"
    if agent_type in EXEMPT_AGENTS:
        allow()
        return

    transcript = data.get("transcript_path")
    # No transcript, no proof either way. Never block on what cannot be checked
    # — but leave a trace: a renamed payload field would otherwise switch the
    # gate off permanently and invisibly.
    if not transcript or not Path(transcript).is_file():
        log_event(agent_type, "allow-no-transcript", f"transcript_path={transcript!r}")
        allow()
        return

    if skill_loaded(transcript):
        allow()
        return

    reason = (
        f"Делегирование в subagent_type={agent_type} без загруженной доктрины. "
        f"CLAUDE.md требует сначала загрузить навык {SKILL_NAME}: вызови "
        f'Skill(skill="{SKILL_NAME}"), прочитай его и повтори этот вызов Agent — '
        "бриф, нарезка слайсов, гейт завершения и лимиты циклов описаны там. "
        "Гейт не распространяется на поиск и справки: "
        f"{', '.join(sorted(EXEMPT_AGENTS))} проходят без навыка."
    )
    log_event(agent_type, "deny", reason)
    deny(reason)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_event(None, "allow-on-error", repr(e))
        allow()
