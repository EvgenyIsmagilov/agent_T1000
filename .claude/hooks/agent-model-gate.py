#!/usr/bin/env python3
"""PreToolUse gate for the Agent and start_session tools: model choice follows task-orchestration.

The skill's "Budgets → Model and effort" table says which model a subagent
gets, but a text rule held only while the model remembered it: a session once
sent 12 `general-purpose` agents without `model`, they inherited the session's
opus and burned the 5-hour limit in minutes. Two checks, both on the call's
own input:

* a built-in agent without its own model (`general-purpose`, `Explore`,
  `Plan`, `claude`) must name `model` explicitly;
* `opus` and `fable` are for `reviewer` and `magi-*` only, unless the most
  recent genuine user message in the transcript names the same family
  ("opus"/"опус", "fable"/"фейбл"). Each family needs its own approval: a
  message that names "opus" does not approve `fable`, and the reverse. Only
  records the user typed count — never the Agent prompt, assistant text, tool
  results, task notifications or messages from other sessions — so the agent
  cannot approve itself.

The same approval rule gates `mcp__ccd_session__start_session` (skill
subsection "Budgets → Sessions"). A session on opus/fable needs the user to
name that family, whether `model` is explicit or inherited from the parent
(the model of the last assistant record in the transcript; `fork` always
inherits). A fresh session without `model` and with an unknown parent is
denied: the skill requires `model` there. Allowed without a check: an offer
(no `prompt`, no `proposal_id`), a bare `proposal_id` (its card was gated when
created), and `target: "cloud"` / `preset` (logged as allow-unchecked-target,
the model is not visible).

Fails open on every uncertainty — unreadable transcript, missing path, any
exception. This is a budget guardrail, not a security boundary. Passing calls
get no output at all, so permission handling stays with the other hooks.

Disable with CLAUDE_AGENT_MODEL_GATE=0.
"""
import json
import os
import re
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn

LOG_PATH = Path.home() / ".claude" / "logs" / "agent-model-gate.jsonl"
SKILL_REF = "~/.claude/skills/task-orchestration/SKILL.md, раздел «Budgets → Model and effort»"
SESSION_REF = "~/.claude/skills/task-orchestration/SKILL.md, раздел «Budgets → Sessions»"
START_SESSION = "mcp__ccd_session__start_session"

# Built-in agents with no `model` of their own: without an explicit `model`
# they run on whatever the env/session gives them. A missing subagent_type
# means general-purpose.
NO_OWN_MODEL_AGENTS = {"general-purpose", "Explore", "Plan", "claude"}

PREMIUM_RES = {
    "opus": re.compile(r"opus|опус", re.IGNORECASE),
    "fable": re.compile(r"fable|фейбл", re.IGNORECASE),
}
INTERRUPT_PREFIX = "[Request interrupted by user"


def log_event(agent_type: str | None, model: str | None, decision: str, reason: str) -> None:
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


def deny(reason: str) -> NoReturn:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))
    sys.exit(0)


def premium_allowed_for(agent_type: str) -> bool:
    return agent_type == "reviewer" or agent_type.startswith("magi-")


def families_in(text: str) -> set[str]:
    return {family for family, rx in PREMIUM_RES.items() if rx.search(text)}


def user_text(rec: Mapping[str, Any]) -> str | None:
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


def last_user_text(path: str | Path) -> str | None:
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


def parent_model(path: str | None) -> str | None:
    """`message.model` of the last main-chain assistant record with a real model, or None."""
    if not path or not Path(path).is_file():
        return None
    model = None
    with Path(path).open() as f:
        for line in f:
            if '"assistant"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            message = rec.get("message")
            if rec.get("type") != "assistant" or rec.get("isSidechain"):
                continue
            candidate = message.get("model") if isinstance(message, dict) else None
            if isinstance(candidate, str) and candidate.startswith("claude-"):
                model = candidate
    return model


def gate_start_session(data: dict[str, Any]) -> None:
    tool_input = data.get("tool_input", {}) or {}
    if not tool_input.get("prompt"):
        # An offer and a bare proposal_id start nothing new to gate; any other
        # promptless shape is allowed but leaves a trace.
        bare = tool_input.get("proposal_id") and not any(
            tool_input.get(k) for k in ("model", "context", "effort"))
        if tool_input.get("initiation") != "offer" and not bare:
            log_event("start_session", None, "allow-unchecked-shape",
                      f"keys={sorted(tool_input)}")
        return
    model = str(tool_input.get("model") or "").strip()
    if tool_input.get("target") == "cloud" or tool_input.get("preset"):
        log_event("start_session", model, "allow-unchecked-target", "cloud/preset: model not visible")
        return

    transcript = data.get("transcript_path")
    fork = tool_input.get("context") == "fork"
    if fork or not model:
        try:
            effective = parent_model(transcript)
        except OSError:
            effective = None
        if not effective:
            if fork:
                log_event("start_session", model, "allow-no-parent-model", f"transcript_path={transcript!r}")
                return
            reason = (
                "start_session context=fresh без model, а модель родителя не определить. "
                "Для fresh model обязателен: передай model (sonnet/haiku по таблице) "
                f"({SESSION_REF})."
            )
            log_event("start_session", model, "deny-no-model", reason)
            deny(reason)
        source = "родителя" if fork else "родителя (model не передан)"
    else:
        effective, source = model, "вызова"

    wanted = families_in(effective)
    if not wanted:
        log_event("start_session", effective, "allow", "non-premium")
        return
    family = "/".join(sorted(wanted))
    if not transcript or not Path(transcript).is_file():
        log_event("start_session", effective, "allow-no-transcript", f"transcript_path={transcript!r}")
        return
    try:
        text = last_user_text(transcript)
    except OSError as e:
        log_event("start_session", effective, "allow-no-transcript",
                  f"transcript_path={transcript!r}, {type(e).__name__}, errno={e.errno}")
        return
    if wanted <= (families_in(text) if text is not None else set()):
        log_event("start_session", effective, "allow-user-premium", f"last user message names {family}")
        return

    reason = (
        f"start_session на {effective} (модель {source}) запрещён: opus/fable в новой сессии "
        f"только с разрешением пользователя ({SESSION_REF}). Передай model=sonnet или haiku "
        f"(для fork это невозможно: fork наследует модель родителя, используй fresh). Если "
        f"{family} действительно нужен — спроси пользователя с ценой и дождись ответа, где он "
        f"сам назовёт {family} («да, {family}»); голое «да» не засчитывается."
    )
    log_event("start_session", effective, "deny-premium", reason)
    deny(reason)


def main() -> None:
    data = json.load(sys.stdin)
    tool = data.get("tool_name")
    if tool not in ("Agent", START_SESSION):
        return
    if os.environ.get("CLAUDE_AGENT_MODEL_GATE") == "0":
        return
    if tool == START_SESSION:
        gate_start_session(data)
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

    wanted = families_in(model)
    if not wanted or premium_allowed_for(agent_type):
        return

    family = "/".join(sorted(wanted))
    transcript = data.get("transcript_path")
    # No transcript, no proof either way: never block on what cannot be
    # checked, but leave a trace so a renamed field does not go unnoticed.
    if not transcript or not Path(transcript).is_file():
        log_event(agent_type, model, "allow-no-transcript", f"transcript_path={transcript!r}")
        return

    text = last_user_text(transcript)
    approved = families_in(text) if text is not None else set()
    if wanted <= approved:
        log_event(agent_type, model, "allow-user-premium", f"last user message names {family}")
        return

    reason = (
        f"model={model} для subagent_type={agent_type} запрещён: opus/fable только для "
        f"reviewer и magi-* ({SKILL_REF}). Возьми sonnet или haiku по таблице. Если "
        f"{family} действительно нужен — спроси пользователя с ценой (количество × модель × "
        f"effort) и дождись ответа, где он сам назовёт {family} («да, {family}»); голое «да» "
        "не засчитывается, разрешение видно только в его последнем сообщении."
    )
    log_event(agent_type, model, "deny-premium", reason)
    deny(reason)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Exception text can quote transcript bytes (UnicodeDecodeError does), so log the type only.
        detail = f", errno={e.errno}" if isinstance(e, OSError) else ""
        log_event(None, None, "allow-on-error", f"{type(e).__name__}{detail}")
    sys.exit(0)
