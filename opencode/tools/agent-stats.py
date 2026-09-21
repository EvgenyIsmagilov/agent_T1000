#!/usr/bin/env python3
"""Digest of the OpenCode subagent run log (claude-hooks.js, event "tool.execute.after").

Ported from ~/.claude/tools/agent-stats.py, trimmed to what OpenCode's log
actually contains. Dropped on purpose, not by oversight:
  - report-contract validation / format-discipline sections — claude-hooks.js
    does not validate subagent reports against a role contract, so there is
    no `contract_status` field to aggregate;
  - orchestration-gate denials — the gate in claude-hooks.js throws on deny,
    it does not log denials to a file;
  - maxTurns "лимит" column — OpenCode agent frontmatter has no maxTurns field.
Kept and OpenCode-only: `cost` (OpenCode prices each message; Claude's log does not).

Read-only, stdlib only. Usage:
    python3 ~/.config/opencode/tools/agent-stats.py [--days N] [--runs N] [--log PATH]
"""
import argparse
import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

RUN_LOG = Path.home() / ".claude" / "logs" / "subagent-runs-opencode.jsonl"


def human(n):
    if n is None:
        return "—"
    for limit, suffix in ((1_000_000, "M"), (1_000, "k")):
        if n >= limit:
            return f"{n / limit:.1f}{suffix}"
    return str(n)


def read_jsonl(path):
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # a half-written line must not kill the report
    return out


def parse_ts(rec):
    try:
        return datetime.fromisoformat(str(rec.get("timestamp", "")).replace("Z", "+00:00"))
    except ValueError:
        return None


def recent(records, days):
    if not days:
        return records
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    kept = []
    for rec in records:
        ts = parse_ts(rec)
        if ts is None or ts >= cutoff:
            kept.append(rec)  # an unparseable timestamp is shown, not dropped
    return kept


def tokens(rec):
    return sum(
        rec.get(k) or 0
        for k in ("input_tokens", "output_tokens", "reasoning_tokens", "cache_read_tokens", "cache_write_tokens")
    )


def flags(rec):
    """Why this run deserves a second look. Empty list = nothing odd."""
    out = []
    if not rec.get("childSessionID"):
        out.append("child-сессия не сматчилась — токены/turns неполные или отсутствуют")
    if not rec.get("verdict"):
        out.append("вердикт не распознан в отчёте")
    return out


def main():
    ap = argparse.ArgumentParser(description="Сводка по прогонам саб-агентов OpenCode")
    ap.add_argument("--days", type=int, default=0, help="только за последние N дней")
    ap.add_argument("--runs", type=int, default=12, help="сколько подозрительных прогонов показать")
    ap.add_argument("--log", type=Path, default=RUN_LOG, help="путь к subagent-runs-opencode.jsonl")
    args = ap.parse_args()

    runs = recent(read_jsonl(args.log), args.days)
    window = f"за {args.days} дн." if args.days else "за всё время"

    if not runs:
        print(f"Прогонов не найдено ({args.log}, {window}).")
        return

    print(f"ПРОГОНЫ {window} — {len(runs)} шт., лог {args.log}\n")
    print(f"{'агент':<22}{'шт':>4}{'ходы med/max':>14}{'токены':>9}{'cost $':>9}  вердикты")

    by_agent = {}
    for rec in runs:
        by_agent.setdefault(rec.get("subagentType") or "?", []).append(rec)

    for agent, group in sorted(by_agent.items(), key=lambda kv: -len(kv[1])):
        turns = [r.get("turns") or 0 for r in group]
        verdicts = {}
        for rec in group:
            key = rec.get("verdict") or "—"
            verdicts[key] = verdicts.get(key, 0) + 1
        summary = " ".join(f"{k}:{v}" for k, v in sorted(verdicts.items()))
        cost = sum(r.get("cost") or 0 for r in group)
        print(
            f"{agent:<22}{len(group):>4}"
            f"{f'{int(statistics.median(turns))}/{max(turns)}':>14}"
            f"{human(sum(tokens(r) for r in group)):>9}"
            f"{cost:>9.3f}  {summary}"
        )

    suspicious = [(rec, flags(rec)) for rec in runs]
    suspicious = [(rec, f) for rec, f in suspicious if f]
    print(f"\nПОДОЗРИТЕЛЬНЫЕ — {len(suspicious)} из {len(runs)}")
    if not suspicious:
        print("  по доступным данным предупреждений нет")
    for rec, why in suspicious[-args.runs:]:
        ts = parse_ts(rec)
        stamp = ts.strftime("%m-%d %H:%M") if ts else "??"
        print(f"  {stamp}  {rec.get('subagentType', '?'):<20} {'; '.join(why)}")

    total_tok = sum(tokens(r) for r in runs)
    total_cost = sum(r.get("cost") or 0 for r in runs)
    seconds = sum(r.get("duration_s") or 0 for r in runs)
    print(f"\nИТОГО  токенов {human(total_tok)}   cost ${total_cost:.3f}   время {seconds / 60:.0f} мин")


if __name__ == "__main__":
    main()
