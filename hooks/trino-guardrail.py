#!/usr/bin/env python3
"""PreToolUse guardrail for the Trino MCP tools (execute_query, explain_query).

Best-effort text-level checks on the SQL string, not a real parser — a determined
or badly-mangled query can still slip past. The real hard boundary is the Trino
ACL on the agent's DB role. This just catches ordinary mistakes and enforces the
attribution comment automatically.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ATTRIBUTION = "-- Claude Code agent"
ALLOWED_WRITE_TARGET = re.compile(r"^(ic\.)?temp\.", re.IGNORECASE)
DDL_DML_KEYWORDS = re.compile(
    r"\b(CREATE|ALTER|DROP|INSERT|UPDATE|DELETE|MERGE|TRUNCATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)
TARGET_NAME = re.compile(r"\b(?:TABLE|INTO|VIEW)\s+(\"?[a-zA-Z0-9_.\"]+)", re.IGNORECASE)
LOG_PATH = Path.home() / ".claude" / "logs" / "trino-queries.jsonl"


def split_statements(sql: str):
    return [s.strip() for s in sql.split(";") if s.strip()]


def log_event(tool_name, query, decision, reason):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "query": query,
            "decision": decision,
            "reason": reason,
        }, ensure_ascii=False) + "\n")


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def allow(updated_query=None):
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }
    if updated_query is not None:
        output["hookSpecificOutput"]["updatedInput"] = {"query": updated_query}
    print(json.dumps(output))
    sys.exit(0)


def main():
    data = json.load(sys.stdin)
    tool_name = data.get("tool_name", "")
    query = data.get("tool_input", {}).get("query", "")

    if not query.strip():
        allow()
        return

    statements = split_statements(query)
    if len(statements) > 1:
        log_event(tool_name, query, "deny", "multiple statements in one call")
        deny(
            "Только один SQL-запрос за вызов. Разбей на отдельные вызовы "
            "execute_query — один statement, один вызов."
        )

    if DDL_DML_KEYWORDS.search(query):
        match = TARGET_NAME.search(query)
        target = match.group(1).strip('"') if match else None
        if not target or not ALLOWED_WRITE_TARGET.match(target):
            log_event(tool_name, query, "deny", f"DDL/DML outside ic.temp (target={target})")
            deny(
                "DDL/DML разрешён только в ic.temp — создание, изменение и удаление "
                "таблиц где-либо ещё запрещено. Если задача требует иного — сообщи "
                "пользователю, не ищи обход."
            )

    updated_query = None
    if ATTRIBUTION.lower() not in query.lower():
        updated_query = f"{ATTRIBUTION}\n{query}"

    log_event(tool_name, updated_query or query, "allow", "")
    allow(updated_query)


if __name__ == "__main__":
    main()
