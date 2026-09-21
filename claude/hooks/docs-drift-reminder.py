#!/usr/bin/env python3
"""PostToolUse nudge: editing an orchestration file should raise the question
of whether docs/orchestration-architecture.md needs updating too.

Cannot judge "significant" — that's a judgment call for whoever is editing,
not a lint rule. So this only reminds, once per (session, file), and never
blocks: the edit has already happened by the time PostToolUse fires.

Fails open on any error: a broken reminder must never break Edit/Write.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
DOC_PATH = CLAUDE_DIR / "docs" / "orchestration-architecture.md"
SEEN_LOG = CLAUDE_DIR / "logs" / "docs-drift-reminders.jsonl"
AGENTS_DIR = CLAUDE_DIR / "agents"

# Files whose meaning is the orchestration design itself, not incidental code.
ORCH_FILES = {
    CLAUDE_DIR / "skills" / "task-orchestration" / "SKILL.md",
    CLAUDE_DIR / "skills" / "code-tier-assessment" / "SKILL.md",
    CLAUDE_DIR / "hooks" / "log-subagent.py",
    CLAUDE_DIR / "hooks" / "require-orchestration.py",
    CLAUDE_DIR / "tools" / "agent-stats.py",
    CLAUDE_DIR / "tools" / "first-pass.py",
}


def is_orchestration_file(path: Path) -> bool:
    if path in ORCH_FILES:
        return True
    return path.parent == AGENTS_DIR and path.suffix == ".md"


def already_reminded(session_id, path: Path) -> bool:
    if not SEEN_LOG.is_file():
        return False
    key = (session_id, str(path))
    with SEEN_LOG.open() as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if (rec.get("session_id"), rec.get("file_path")) == key:
                return True
    return False


def remember(session_id, path: Path):
    SEEN_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SEEN_LOG.open("a") as f:
        f.write(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "file_path": str(path),
        }, ensure_ascii=False) + "\n")


def remind(path: Path):
    context = (
        f"Ты изменил файл оркестрации ({path}). Если изменение существенное "
        "(новая роль или тир, другая механика эскалации, другой формат брифа "
        f"или лога) — обнови {DOC_PATH}. Если это мелкая правка, ничего не делай."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": context,
            "systemMessage": f"Напоминание: проверь {DOC_PATH.name} после правки оркестрации.",
        }
    }))


def main():
    data = json.load(sys.stdin)
    if data.get("tool_name") not in ("Edit", "Write"):
        return
    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path")
    if not file_path:
        return
    path = Path(file_path)
    if not is_orchestration_file(path):
        return
    session_id = data.get("session_id")
    if already_reminded(session_id, path):
        return
    remind(path)
    remember(session_id, path)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # a broken reminder must never break Edit/Write
