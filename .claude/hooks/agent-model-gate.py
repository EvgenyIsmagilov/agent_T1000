#!/usr/bin/env python3
"""PreToolUse gate for the Agent tool: model choice follows task-orchestration.

The skill's "Budgets → Model and effort" table says which model a subagent
gets, but a text rule held only while the model remembered it: a session once
sent 12 `general-purpose` agents without `model`, they inherited the session's
opus and burned the 5-hour limit in minutes. Two checks, both on the call's
own input:

* a built-in agent without its own model (`general-purpose`, `Explore`,
  `Plan`, `claude`) must name `model` explicitly;
* `opus` is for `reviewer` and `magi-*` only, unless the most recent genuine
  user message in the transcript mentions opus ("opus"/"опус"). Only records
  the user typed count — never the Agent prompt, assistant text, tool results,
  task notifications or messages from other sessions — so the agent cannot
  approve itself.

Fails open on every uncertainty — unreadable transcript, missing path, any
exception. This is a budget guardrail, not a security boundary. Passing calls
get no output at all, so permission handling stays with the other hooks.

Disable with CLAUDE_AGENT_MODEL_GATE=0.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path.home() / ".claude" / "logs" / "agent-model-gate.jsonl"
SKILL_REF = "~/.claude/skills/task-orchestration/SKILL.md, раздел «Budgets → Model and effort»"

# Built-in agents with no `model` of their own: without an explicit `model`
# they run on whatever the env/session gives them. A missing subagent_type
# means general-purpose.
NO_OWN_MODEL_AGENTS = {"general-purpose", "Explore", "Plan", "claude"}

OPUS_RE = re.compile(r"opus|опус", re.IGNORECASE)
INTERRUPT_PREFIX = "[Request interrupted by user"


def log_event(agent_type, model, decision, reason):
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a") as f:
            f.write(json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent_type": agent_type,
                "model": model,
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
    }, ensure_ascii=False))
    sys.exit(0)


def opus_allowed_for(agent_type):
    return agent_type == "reviewer" or agent_type.startswith("magi-")


def user_text(rec):
    """Typed text of a genuine user record, or None for anything else.

    Skipped: tool results, skill bodies and other injected meta records,
    sidechains, compact summaries and interrupt markers.
    """
    if rec.get("type") != "user":
        return None
    if rec.get("isMeta") or rec.get("isSidechain") or rec.get("isCompactSummary"):
        return None
    message = rec.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        text = "\n".join(
            b.get("text") or "" for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    else:
        return None
    if not text.strip() or text.lstrip().startswith(INTERRUPT_PREFIX):
        return None
    return text


def last_user_text(path):
    """Most recent message the user typed, or None if there is none.

    Only records tagged `origin.kind == "human"` count (written since CLI
    2.1.187). Task notifications carry another kind; messages from other
    sessions, app notices and compact summaries carry no `origin` at all — a
    side session started from a brief that says "opus" has no human record,
    and must not read that brief as approval. A session resumed from an older
    transcript gets tagged records for every new message, so this costs at
    most one extra question there.
    """
    last_human = None
    with Path(path).open() as f:
        for line in f:
            if '"human"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            origin = rec.get("origin")
            if not isinstance(origin, dict) or origin.get("kind") != "human":
                continue
            text = user_text(rec)
            if text is not None:
                last_human = text
    return last_human


def main():
    data = json.load(sys.stdin)
    if data.get("tool_name") != "Agent":
        return
    if os.environ.get("CLAUDE_AGENT_MODEL_GATE") == "0":
        return

    tool_input = data.get("tool_input", {}) or {}
    agent_type = tool_input.get("subagent_type") or "general-purpose"
    model = str(tool_input.get("model") or "").strip()

    if not model:
        if agent_type in NO_OWN_MODEL_AGENTS:
            reason = (
                f"Вызов subagent_type={agent_type} без model. У этого агента нет своей "
                "модели: он пойдёт на модели и effort по умолчанию, а не на выбранных "
                "под задачу. Передай model и effort из таблицы "
                f"в {SKILL_REF}: поиск/сводка — haiku, анализ — sonnet. Самая дешёвая "
                "подходящая строка, выше — только после слабого результата."
            )
            log_event(agent_type, model, "deny-no-model", reason)
            deny(reason)
        return

    if not OPUS_RE.search(model) or opus_allowed_for(agent_type):
        return

    transcript = data.get("transcript_path")
    # No transcript, no proof either way: never block on what cannot be
    # checked, but leave a trace so a renamed field does not go unnoticed.
    if not transcript or not Path(transcript).is_file():
        log_event(agent_type, model, "allow-no-transcript", f"transcript_path={transcript!r}")
        return

    text = last_user_text(transcript)
    if text is not None and OPUS_RE.search(text):
        log_event(agent_type, model, "allow-user-opus", "last user message mentions opus")
        return

    reason = (
        f"model={model} для subagent_type={agent_type} запрещён: opus только для "
        f"reviewer и magi-* ({SKILL_REF}). Возьми sonnet или haiku по таблице. Если "
        "opus действительно нужен — спроси пользователя с ценой (количество × модель × "
        "effort) и дождись ответа, где он сам назовёт opus («да, opus»); голое «да» "
        "не засчитывается, разрешение видно только в его последнем сообщении."
    )
    log_event(agent_type, model, "deny-opus", reason)
    deny(reason)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_event(None, None, "allow-on-error", repr(e))
    sys.exit(0)
