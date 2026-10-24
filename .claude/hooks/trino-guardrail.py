#!/usr/bin/env python3
"""PreToolUse scope guard for the Trino MCP tools (execute_query, explain_query).

Trino MCP is registered globally (~/.claude.json top-level mcpServers), so it
would otherwise be usable from every project on this machine. This hook confines
actual use to the configured project (main checkout plus any of its git
worktrees) — a session in an unrelated project has no business querying the
company warehouse. Added 2026-09-15 alongside the data-catalog work.

The hook reads the project root from the TRINO_GUARDRAIL_PROJECT_ROOT environment
variable. If the variable is unset or empty, the hook denies every Trino call.

Query-level enforcement (single-statement rule, DDL/DML confined to ic.temp,
SHOW CREATE whitelist, attribution) used to live here too, but the configured project now
carries its own committed copy of that logic in .claude/hooks/trino-guardrail.py,
registered via its own .claude/settings.json — every teammate gets it without a
personal file. So this hook passes through silently for sessions already inside
the configured project (the repo's own hook takes it from there) and only ever makes a
decision — deny — for sessions outside it. Running full enforcement in both
places would double-log every query and race on which hook's updatedInput (the
auto-prepended attribution) wins.
"""
import json
import os
import sys
from pathlib import Path

# The repository this guard defers to, so its own hook owns enforcement there.
# Point TRINO_GUARDRAIL_PROJECT_ROOT at that checkout; with nothing set the
# guard treats every session as out of scope and denies Trino calls everywhere.
PROJECT_ROOT = Path(os.environ.get("TRINO_GUARDRAIL_PROJECT_ROOT", "/nonexistent")).expanduser()


def in_project_scope(cwd_value):
    if not cwd_value:
        return False
    try:
        cwd = Path(cwd_value).resolve()
        root = PROJECT_ROOT.resolve()
    except OSError:
        return False
    return cwd == root or root in cwd.parents


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def main():
    data = json.load(sys.stdin)
    cwd = data.get("cwd", "")

    if not os.environ.get("TRINO_GUARDRAIL_PROJECT_ROOT"):
        deny("Trino MCP заблокирован: не задана переменная окружения TRINO_GUARDRAIL_PROJECT_ROOT (путь к проекту).")

    if in_project_scope(cwd):
        # Никакого решения — репозиторный хук настроенного проекта уже enforces.
        sys.exit(0)

    deny(
        "Trino MCP ограничен настроенным проектом и его воркtree-ами. Эта "
        "сессия работает в другом проекте — обращение к складу данных здесь "
        "не предусмотрено. Если задача действительно требует Trino, скажи "
        "пользователю, не ищи обход."
    )


if __name__ == "__main__":
    main()
