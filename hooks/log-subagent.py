#!/usr/bin/env python3
"""SubagentStop hook: one JSONL line per finished subagent run.

Answers what the orchestration skill currently cannot answer after the fact:
which agents ran, how they ended, and what they cost in turns and tokens.

Logs metadata only — never the report text — so nothing sensitive lands in the
log. Fails open on any error: observability must never break a run.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

# Row format generation, stamped on every entry so analysis never has to
# infer it from which fields happen to be missing.
#   1 — before the agent-transcript fix: no `model`, no contract validation,
#       and turns/tokens that may describe the parent session, not the run.
#   2 — contract validation and `model`, but unstamped.
#   3 — stamped, `hook_event`, `verdict_status`, non-subagent events skipped.
#   4 — brief identity: `slice_id`, `tier`, `previous_failure_class`.
SCHEMA = 4

DEFAULT_LOG = Path.home() / ".claude" / "logs" / "subagent-runs.jsonl"
DEFAULT_SKIPPED_LOG = Path.home() / ".claude" / "logs" / "subagent-runs-skipped.jsonl"

HEADING_RE = re.compile(r"^ {0,3}###\s+(.+?)\s*$")

# Fields the orchestrator names in the delegation brief. Matched on their
# own line, optionally as a list item and optionally backticked, so prose
# that merely mentions a slice cannot be mistaken for a declaration.
BRIEF_FIELDS = {
    "slice_id": r"[A-Za-z0-9._-]{1,64}",
    "tier": r"T[123]",
    "previous_failure_class": r"executor|spec|env|slicing",
}
BRIEF_RE = {
    name: re.compile(
        r"^[ \t]*[-*]?[ \t]*`?" + name + r"`?[ \t]*[:=][ \t]*`?(" + pattern + r")`?",
        re.MULTILINE | re.IGNORECASE,
    )
    for name, pattern in BRIEF_FIELDS.items()
}


class RoleContract(TypedDict, total=False):
    heading: str
    statuses: tuple[str, ...]
    required: tuple[str, ...]
    conditional: dict[str, tuple[str, ...]]


# Small, explicit mirror of the role contracts in ~/.claude/agents/*.md.
# Unknown and built-in agents are deliberately exempt: their reports have no
# custom schema to validate.
ROLE_CONTRACTS: dict[str, RoleContract] = {
    "web-researcher-fast": {
        "heading": "result",
        "statuses": ("DONE", "PARTIAL", "BLOCKED"),
        "required": ("result", "answer", "sources"),
    },
    "docs-researcher-deep": {
        "heading": "result",
        "statuses": ("DONE", "PARTIAL", "BLOCKED"),
        "required": ("result", "answer", "findings", "recommended action", "sources"),
    },
    "code-writer-t1": {
        "heading": "result",
        "statuses": ("DONE", "PARTIAL", "BLOCKED"),
        "required": ("result", "changes", "verification"),
    },
    "code-writer-t2": {
        "heading": "result",
        "statuses": ("DONE", "PARTIAL", "BLOCKED"),
        "required": ("result", "changes", "verification"),
    },
    "code-writer-t3": {
        "heading": "result",
        "statuses": ("DONE", "PARTIAL", "BLOCKED"),
        "required": ("result", "changes", "verification"),
    },
    "bug-investigator": {
        "heading": "result",
        "statuses": ("CONFIRMED", "PROBABLE", "INCONCLUSIVE", "BLOCKED"),
        "required": ("result", "root cause", "evidence", "fix direction", "verification"),
    },
    "test-runner": {
        "heading": "result",
        "statuses": ("PASS", "FAIL", "PARTIAL", "BLOCKED"),
        "required": ("result", "checks", "next action"),
        "conditional": {"FAIL": ("failures",), "PARTIAL": ("failures",)},
    },
    "code-reviewer": {
        "heading": "verdict",
        "statuses": ("APPROVE", "REQUEST_CHANGES", "BLOCKED"),
        "required": ("verdict", "review scope", "findings", "challenges performed", "final assessment"),
    },
    "sql-data-reviewer": {
        "heading": "verdict",
        "statuses": ("APPROVE", "REQUEST_CHANGES", "BLOCKED"),
        "required": (
            "verdict", "query profile", "resource-risk summary", "findings",
            "strongest challenges performed", "recommended verification", "final assessment",
        ),
    },
}


def log_path():
    return Path(os.environ.get("CLAUDE_SUBAGENT_LOG", DEFAULT_LOG))


def skipped_log_path():
    return Path(os.environ.get("CLAUDE_SUBAGENT_SKIPPED_LOG", DEFAULT_SKIPPED_LOG))


def skip_reason(data):
    """Why this event is not a subagent run, or None when it is one.

    The hook is registered for `SubagentStop` only, yet it also receives
    events fired at the end of a main-agent turn: no agent type, no agent
    transcript, no model, no turns, no tokens. Logged as runs they are
    indistinguishable from a subagent that cost nothing, and they poison
    every denominator built on the file. Identity is the test, not the
    event name, which these payloads do not reliably carry.
    """
    if not (data.get("agent_type") or "").strip():
        return "no_agent_type"
    path = data.get("agent_transcript_path")
    if not path or not Path(path).is_file():
        return "no_agent_transcript"
    return None


def log_skipped(data, reason):
    """Keep skipped events visible, so a payload change cannot silently
    drop real runs. Metadata only, same as the main log."""
    report = data.get("last_assistant_message")
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": data.get("session_id"),
        "hook_event": data.get("hook_event_name"),
        "schema": SCHEMA,
        "agent_type": data.get("agent_type"),
        "reason": reason,
        "report_chars": len(report) if isinstance(report, str) else 0,
        "cwd": data.get("cwd"),
    }
    path = skipped_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def effort_level(effort):
    """`effort` arrives as {"level": "max"} — keep the column scalar."""
    if isinstance(effort, dict):
        return effort.get("level")
    return effort


def first_user_text(path) -> str:
    """The delegation prompt, which the subagent transcript opens with."""
    with Path(path).open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            message = rec.get("message")
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            return ""
    return ""


def brief_identity(path) -> dict:
    """Which slice this run belongs to, read from the brief it was given.

    Rows are otherwise anonymous: nothing ties an implementer run to the
    test run and the review of the same slice, so no metric can ask whether
    a slice passed on the first attempt. The orchestration skill has the
    orchestrator name the slice in the brief; the prompt is preserved in
    the transcript, so reading it here costs the orchestrator nothing and
    cannot be skewed by what an agent says about itself afterwards.

    `tier` is the tier the brief asked for. Compared against `model` it
    shows whether the call actually used the tier that was routed.
    """
    identity = dict.fromkeys(BRIEF_RE)
    if not path or not Path(path).is_file():
        return identity
    prompt = first_user_text(path)
    for name, pattern in BRIEF_RE.items():
        match = pattern.search(prompt)
        if match:
            identity[name] = match.group(1)
    return identity


def agent_id(data):
    """Stable id of this run, and the way back to its transcript.

    The transcript is named `agent-<id>.jsonl`, so the path carries the id
    the payload does not reliably provide. Without it rows are anonymous:
    they cannot be deduplicated, and nothing can be re-checked against the
    transcript afterwards. Schema 1 logged it; schema 2 dropped it.
    """
    path = data.get("agent_transcript_path")
    if path:
        stem = Path(path).stem
        return stem[len("agent-"):] if stem.startswith("agent-") else stem
    return data.get("agent_id")


def verdict_status(contract_status: str, verdict: str | None) -> str:
    """Whether the run's outcome is known, as one self-describing column.

    `verdict: null` alone is ambiguous: a built-in agent has no report
    contract and never yields one, while a contract-bearing agent yielding
    none means the outcome was lost. Any metric built on outcomes has to
    tell those apart, and should not have to rederive the rule.
    """
    if verdict is not None:
        return "captured"
    if contract_status == "exempt":
        return "not_applicable"
    return "lost"


def report_metadata_status(report_present: bool, report: object) -> str:
    """Classify payload availability without treating falsey values alike."""
    if not report_present or report is None or not isinstance(report, str):
        return "unavailable"
    if not report.strip():
        return "empty"
    return "present"


def structural_text(line: str) -> str | None:
    """Strip only Markdown's permitted 0-3 leading spaces."""
    prefix = line[:len(line) - len(line.lstrip(" \t"))]
    if "\t" in prefix or len(prefix) > 3:
        return None
    return line[len(prefix):]


def fence_marker(line: str) -> tuple[str, int] | None:
    """Opening fence marker, preserving its delimiter character and length."""
    stripped = structural_text(line)
    if not stripped or stripped[0] not in "`~":
        return None
    marker = stripped[0]
    length = len(stripped) - len(stripped.lstrip(marker))
    return (marker, length) if length >= 3 else None


def next_fence(
    line: str, fence: tuple[str, int] | None
) -> tuple[tuple[str, int] | None, bool]:
    """Advance a fenced block; closing must match its marker and length."""
    if fence is None:
        opener = fence_marker(line)
        return opener, opener is not None
    marker, length = fence
    stripped = structural_text(line)
    if stripped is None:
        return fence, False
    run = len(stripped) - len(stripped.lstrip(marker))
    if run >= length and not stripped[run:].strip():
        return None, True
    return fence, False


def visible_lines(report: str) -> list[tuple[int, str]]:
    """Lines outside Markdown fences and block quotes, with their positions."""
    fence = None
    lines = []
    for index, line in enumerate(report.splitlines()):
        stripped = line.lstrip()
        fence, boundary = next_fence(line, fence)
        if boundary or fence is not None:
            continue
        if stripped.startswith(">"):
            continue
        lines.append((index, line))
    return lines


def sections(report: str) -> dict[str, bool]:
    """Return headings outside fences/quotes and whether each has content."""
    found = {}
    current = None
    fence = None
    for line in report.splitlines():
        stripped = line.lstrip()
        fence, boundary = next_fence(line, fence)
        if boundary:
            continue
        if fence is not None:
            if current and stripped:
                found[current] = True
            continue
        if stripped.startswith(">"):
            if current and stripped:
                found[current] = True
            continue
        match = HEADING_RE.match(line)
        if match:
            current = match.group(1).strip().lower().rstrip(":")
            found[current] = False
            continue
        if current and stripped:
            found[current] = True
    return found


def validate_report(
    agent_type: str | None, report_present: bool, report: object
) -> tuple[str | None, str, str, list[str]]:
    """Pure, structural validation; report content is never retained or logged."""
    metadata = report_metadata_status(report_present, report)
    contract = ROLE_CONTRACTS.get(agent_type)
    if contract is None:
        return None, metadata, "exempt", []
    if metadata == "unavailable":
        if not report_present:
            reason = "report_missing"
        elif report is None:
            reason = "report_null"
        else:
            reason = "report_nonstring"
        return None, metadata, "unavailable", [reason]
    if metadata == "empty":
        return None, metadata, "invalid", ["report_empty"]
    assert isinstance(report, str)

    lines = visible_lines(report)
    reasons = []
    if not lines:
        return None, metadata, "invalid", ["no_visible_report"]
    meaningful = [(index, line) for index, line in lines if line.strip()]

    # Extracting the verdict and judging the contract are separate questions.
    # Agents routinely open with a line of preamble before the required
    # heading; that is a contract violation, but the verdict sitting right
    # under the heading is still good data and must not be discarded with it.
    # So locate the heading anywhere in the report and record where it was
    # found as its own reason.
    heading = contract["heading"]
    start = None
    for position, (_, line) in enumerate(meaningful):
        match = HEADING_RE.match(line)
        if match and match.group(1).strip().lower().rstrip(":") == heading:
            start = position
            break

    verdict = None
    if start is None:
        reasons.append("missing_" + heading)
    else:
        if start > 0:
            reasons.append("preamble_before_" + heading)
        if len(meaningful) > start + 1:
            status_line = structural_text(meaningful[start + 1][1]) or ""
            statuses = "|".join(contract["statuses"])
            match = re.match(r"^`?(" + statuses + r")`?(?=\s|—|-|$)", status_line)
            if match:
                verdict = match.group(1)
        if verdict is None:
            reasons.append("missing_or_invalid_status")

    found = sections(report)
    required = list(contract["required"])
    if verdict:
        required.extend(contract.get("conditional", {}).get(verdict, ()))
    for heading in required:
        if heading not in found:
            reasons.append("missing_section:" + heading.replace(" ", "_"))
        elif not found[heading]:
            reasons.append("empty_section:" + heading.replace(" ", "_"))
    reasons = reasons[:8]
    return verdict, metadata, "valid" if not reasons else "invalid", reasons


def transcript_stats_with_retry(path, attempts=6, delay=0.1):
    """Read the transcript, waiting for its final turn to be flushed.

    The hook fires before the subagent's last turn reaches the file, so a
    first read typically ends on `tool_use` and misses the closing message.
    Retry only while that tell-tale is there: normally one extra read, at
    most ~0.5s, well inside the hook's 10s timeout.
    """
    stats = transcript_stats(path)
    for _ in range(attempts - 1):
        if stats.get("stop_reason") != "tool_use":
            break
        time.sleep(delay)
        stats = transcript_stats(path)
    return stats


def transcript_stats(path):
    """Turns, token totals, and wall time from the subagent's own transcript.

    Must be fed `agent_transcript_path`, not `transcript_path`: the latter is
    the parent session's transcript, and using it reports the whole session's
    cost as if the subagent had spent it. When the path is missing, report
    nulls rather than zeros — "not measured" is not "cost nothing".
    """
    stats = {
        "turns": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "duration_s": None,
        # From the transcript, not the payload: the event carries no model and
        # no stop_reason. `max_tokens` here means the agent was cut off.
        "model": None,
        "stop_reason": None,
    }
    if not path or not Path(path).is_file():
        return dict.fromkeys(stats, None)

    first = last = None
    with Path(path).open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ts = rec.get("timestamp")
            if ts:
                first = first or ts
                last = ts
            message = rec.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
            if not usage:
                continue
            stats["turns"] += 1
            stats["input_tokens"] += usage.get("input_tokens", 0)
            stats["output_tokens"] += usage.get("output_tokens", 0)
            stats["cache_creation_tokens"] += usage.get("cache_creation_input_tokens", 0)
            stats["cache_read_tokens"] += usage.get("cache_read_input_tokens", 0)
            # Last turn wins: that is the model that produced the report and
            # the reason the agent actually stopped.
            stats["model"] = message.get("model") or stats["model"]
            stats["stop_reason"] = message.get("stop_reason") or stats["stop_reason"]

    if first and last:
        try:
            t0 = datetime.fromisoformat(first.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(last.replace("Z", "+00:00"))
            stats["duration_s"] = round((t1 - t0).total_seconds(), 1)
        except ValueError:
            pass
    return stats


def main():
    data = json.load(sys.stdin)
    if data.get("hook_event_name") not in (None, "SubagentStop"):
        return

    reason = skip_reason(data)
    if reason:
        log_skipped(data, reason)
        return

    report_present = "last_assistant_message" in data
    report = data.get("last_assistant_message")
    verdict, metadata, contract_status, contract_reasons = validate_report(
        data.get("agent_type"), report_present, report
    )

    stats = transcript_stats_with_retry(data.get("agent_transcript_path"))
    identity = brief_identity(data.get("agent_transcript_path"))
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": data.get("session_id"),
        "hook_event": data.get("hook_event_name"),
        "schema": SCHEMA,
        "agent_id": agent_id(data),
        "agent_type": data.get("agent_type"),
        "model": stats.pop("model"),
        "effort": effort_level(data.get("effort")),
        "verdict": verdict,
        "verdict_status": verdict_status(contract_status, verdict),
        "slice_id": identity["slice_id"],
        "tier": identity["tier"],
        "previous_failure_class": identity["previous_failure_class"],
        "report_metadata_status": metadata,
        "contract_status": contract_status,
        "contract_reasons": contract_reasons,
        "stop_reason": stats.pop("stop_reason"),
        "report_chars": len(report) if isinstance(report, str) else 0,
        "cwd": data.get("cwd"),
    }
    entry.update(stats)

    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never let logging affect the subagent's result
    sys.exit(0)
