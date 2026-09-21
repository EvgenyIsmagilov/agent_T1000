#!/usr/bin/env python3
"""PostToolUseFailure hook: record MCP server failures and tell Claude why.

Answers what nothing else currently answers: which MCP server is degraded,
since when, and whether retrying is pointless. An expired OAuth token
surfaces today only as a wall of server text in the tool result, so the same
dead server gets called again and again inside one task.

Two outputs per failure:
  - one JSONL row in ~/.claude/logs/mcp-failures.jsonl (durable, greppable);
  - one `additionalContext` line next to the tool error, so the running turn
    learns the server is down and what to do instead of retrying blindly.

Never reconnects and never runs a shell command. Upstream (ECC) ships that
behind an opt-in env var holding a shell string; that is a shell-execution
path controlled by the environment, and reauthorizing an MCP server is the
user's action, not a hook's.

Logs metadata plus a masked, truncated error excerpt — never tool input,
never an unmasked token. Fails open on any error: observability must never
break a run. Kill switch: CLAUDE_MCP_HEALTH=0.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Row format generation, stamped on every entry so analysis never has to
# infer it from which fields happen to be missing.
#   1 — server, failure class, consecutive count, masked excerpt.
SCHEMA = 1

LOG = Path.home() / ".claude" / "logs" / "mcp-failures.jsonl"
STATE = Path.home() / ".claude" / "logs" / "mcp-health-state.json"

# A run of failures is "consecutive" only while they keep arriving. After a
# quiet stretch the count starts over, so a server that recovered on its own
# does not carry yesterday's tally into today. Time-based instead of a
# PostToolUse success hook: that would spawn a process on every successful
# MCP call, and there are a lot of those.
RESET_AFTER_S = 1800

# Ordered: the first pattern that matches wins, so specific codes are tried
# before the generic transport catch-all.
FAILURE_CLASSES = [
    ("auth", re.compile(r"\b401\b|unauthori[sz]ed|auth(?:entication)?\s+(?:failed|expired|invalid)|expired token", re.I)),
    ("forbidden", re.compile(r"\b403\b|forbidden|permission denied", re.I)),
    ("rate_limit", re.compile(r"\b429\b|rate.?limit|too many requests", re.I)),
    ("unavailable", re.compile(r"\b(?:500|502|503)\b|service unavailable|overloaded|temporarily unavailable|internal server error", re.I)),
    ("transport", re.compile(r"ECONNREFUSED|ENOTFOUND|EAI_AGAIN|timed? ?out|socket hang up|connection (?:failed|lost|reset|closed)", re.I)),
]

ADVICE = {
    "auth": (
        "the session's credentials for it are rejected or expired. Retrying "
        "will fail the same way. Claude cannot reauthorize from here — tell "
        "the user, and continue the task without this server or stop if it is "
        "essential."
    ),
    "forbidden": (
        "the credentials are accepted but this call is not permitted. Retrying "
        "the same call will fail again; a different call or wider access is needed."
    ),
    "rate_limit": "it is rate-limited. Wait before the next call, or batch the remaining work.",
    "unavailable": "the server itself is failing, not the request. Retrying immediately will not help.",
    "transport": "the connection did not hold. One retry is reasonable; a second failure means the server is down.",
    "unknown": "the cause is not one this hook recognizes. Read the error before retrying.",
}

# Masked before anything is written or shown. An MCP error string can quote
# the request that produced it, headers included.
SECRETS = [
    (re.compile(r"(bearer\s+)[A-Za-z0-9._\-~+/]{8,}", re.I), r"\1***"),
    (re.compile(r"((?:token|secret|password|passwd|api[_-]?key|access[_-]?key|authorization)\"?\s*[:=]\s*\"?)[^\s\"',;&]{4,}", re.I), r"\1***"),
    (re.compile(r"(https?://)[^\s/@]+:[^\s/@]+@", re.I), r"\1***:***@"),
    (re.compile(r"\b(eyJ[A-Za-z0-9_-]{6,}\.)[A-Za-z0-9._-]{8,}"), r"\1***"),
]

EXCERPT_LIMIT = 300


def mask(text: str) -> str:
    for pattern, replacement in SECRETS:
        text = pattern.sub(replacement, text)
    return text


def server_of(tool_name: str):
    """`mcp__<server>__<tool>` -> server, or None for a non-MCP tool."""
    if not tool_name.startswith("mcp__"):
        return None
    parts = tool_name[5:].split("__")
    return parts[0] if len(parts) >= 2 and parts[0] else None


def classify(text: str) -> str:
    for name, pattern in FAILURE_CLASSES:
        if pattern.search(text):
            return name
    return "unknown"


def load_state() -> dict:
    try:
        state = json.loads(STATE.read_text())
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, indent=1, sort_keys=True))
    except Exception:
        pass


def main() -> None:
    if os.environ.get("CLAUDE_MCP_HEALTH") == "0":
        return

    payload = json.loads(sys.stdin.read() or "{}")
    server = server_of(str(payload.get("tool_name") or ""))
    if not server:
        return

    # A cancelled or aborted call says nothing about the server's health.
    if payload.get("is_interrupt"):
        return

    raw = payload.get("error")
    if not isinstance(raw, str):
        raw = json.dumps(raw, ensure_ascii=False) if raw else ""
    excerpt = mask(" ".join(raw.split()))[:EXCERPT_LIMIT]
    failure_class = classify(excerpt)

    now = time.time()
    state = load_state()
    previous = state.get(server) or {}
    last_seen = previous.get("last_seen") or 0
    fresh = (now - last_seen) <= RESET_AFTER_S and previous.get("class") == failure_class
    count = int(previous.get("count") or 0) + 1 if fresh else 1

    state[server] = {
        "class": failure_class,
        "count": count,
        "last_seen": now,
        "first_seen": previous.get("first_seen") if fresh else now,
    }
    save_state(state)

    row = {
        "schema": SCHEMA,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session_id": payload.get("session_id"),
        "server": server,
        "tool": payload.get("tool_name"),
        "class": failure_class,
        "consecutive": count,
        "duration_ms": payload.get("duration_ms"),
        "excerpt": excerpt,
    }
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass

    repeat = f" This is failure {count} in a row for this server." if count > 1 else ""
    note = (
        f"MCP server `{server}` failed ({failure_class}): {ADVICE[failure_class]}"
        f"{repeat} Recorded in ~/.claude/logs/mcp-failures.jsonl."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUseFailure",
            "additionalContext": note,
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Fail open, always. A broken health check must never be the reason a
        # turn stops; the tool already failed on its own.
        pass
    sys.exit(0)
