#!/usr/bin/env python3
"""One-off: recover verdicts for old run-log rows from subagent transcripts.

Reads the run log, writes a NEW file with the same rows in the same order;
never touches the log itself. Prints counts only, never report text.
"""
import argparse
import importlib.util
import json
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from types import ModuleType

CLAUDE_DIR = Path.home() / ".claude"
HOOK_PATH = CLAUDE_DIR / "hooks" / "log-subagent.py"


def load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("log_subagent", HOOK_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def last_assistant_text(path: Path, until: datetime | None = None) -> str | None:
    """Last non-empty assistant text of the final run in a transcript.

    Run boundaries as in the hook's note_handback: only user/assistant records
    count, and a new user instruction (not a tool_result) resets the text.
    Records later than `until` belong to a later run and are ignored.
    """
    text = None
    with path.open() as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            message = rec.get("message") if isinstance(rec, dict) else None
            if rec.get("type") not in ("user", "assistant") or not isinstance(message, dict):
                continue
            ts = parse_ts(rec.get("timestamp"))
            if until and ts and ts > until:
                break
            content = message.get("content")
            if message.get("role") == "user":
                if not (isinstance(content, list)
                        and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)):
                    text = None
                continue
            if message.get("role") != "assistant":
                continue
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
            for block in blocks if isinstance(blocks, list) else []:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                    and block["text"].strip()
                ):
                    text = block["text"]
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=CLAUDE_DIR / "logs" / "subagent-runs.jsonl")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--projects-dir", type=Path, default=CLAUDE_DIR / "projects")
    args = parser.parse_args()

    if args.out.resolve() == args.log.resolve():
        print("refusing: --out is the same file as --log", file=sys.stderr)
        return 2

    hook = load_hook()
    rows = [json.loads(line) for line in args.log.read_text().splitlines() if line.strip()]
    transcripts = {
        p.stem[len("agent-"):]: p for p in args.projects_dir.glob("*/*/subagents/agent-*.jsonl")
    }
    # Продолженный агент дописывает в один транскрипт, поэтому отчёт из него
    # принадлежит только последней строке с этим agent_id.
    last_index = {row.get("agent_id"): i for i, row in enumerate(rows)}
    counts: Counter[str] = Counter()

    for i, row in enumerate(rows):
        counts["scanned"] += 1
        if row.get("verdict") is not None or row.get("agent_type") not in hook.ROLE_CONTRACTS:
            continue
        if row.get("agent_id") is None:
            counts["skipped_null_id"] += 1
            continue
        if last_index[row.get("agent_id")] != i:
            counts["skipped_ambiguous"] += 1
            continue
        path = transcripts.get(row.get("agent_id"))
        if path is None:
            counts["no_transcript"] += 1
            continue
        source = "handback"
        report = hook.transcript_stats(str(path))["handback"]
        if report is None:
            source, report = "last_assistant_message", last_assistant_text(path, parse_ts(row.get("timestamp")))
        if report is None:
            counts["no_report"] += 1
            continue
        verdict, metadata, contract, reasons = hook.validate_report(row["agent_type"], True, report)
        if verdict is None:
            counts["still_no_verdict"] += 1
            continue
        row.update(
            verdict=verdict,
            verdict_status=hook.verdict_status(contract, verdict),
            report_metadata_status=metadata,
            report_chars=len(report),
            contract_status=contract,
            contract_reasons=reasons,
            verdict_backfilled=date.today().isoformat(),
        )
        if row.get("report_source") in (None, "none"):
            row["report_source"] = source
        counts["backfilled"] += 1

    args.out.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    for key in ("scanned", "backfilled", "no_transcript", "no_report", "still_no_verdict", "skipped_ambiguous", "skipped_null_id"):
        print(f"{key}: {counts[key]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
