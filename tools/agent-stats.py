#!/usr/bin/env python3
"""Digest of the subagent run log — the half of the hooks nothing ever read.

`log-subagent.py` has been writing one JSONL line per subagent run (turns,
tokens, verdict, stop reason) and `require-orchestration.py` one per blocked
delegation. Both were write-only: the data existed, nothing looked at it.

This prints what the orchestration doctrine actually needs: what a role costs,
which reports failed their role contract, and which agents used their turn
budget. Only an actual `max_tokens` stop proves token truncation; a turn cap is
a budget warning, and legacy records remain explicitly unvalidated.

Read-only, stdlib only. Usage:
    python3 ~/.claude/tools/agent-stats.py [--days N] [--runs N] [--log PATH]
"""
import argparse
import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
RUN_LOG = CLAUDE_DIR / "logs" / "subagent-runs.jsonl"
GATE_LOG = CLAUDE_DIR / "logs" / "orchestration-gate.jsonl"
AGENTS_DIR = CLAUDE_DIR / "agents"

NEAR_CAP = 0.9  # within 10% of maxTurns: the run nearly died of old age


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


def agent_caps():
    """maxTurns per agent, straight from the frontmatter that defines it."""
    caps = {}
    for path in sorted(AGENTS_DIR.glob("*.md")):
        name = cap = None
        for line in path.read_text(errors="replace").splitlines()[1:40]:
            if line.strip() == "---":
                break
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("maxTurns:"):
                try:
                    cap = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
        if name:
            caps[name] = cap
    return caps


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
        for k in ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens")
    )


def flags(rec, caps):
    """Why this run deserves a second look. Empty list = nothing odd."""
    out = []
    agent = rec.get("agent_type")
    cap = caps.get(agent)
    turns = rec.get("turns") or 0
    if "contract_status" in rec:
        status = rec.get("contract_status")
        metadata = rec.get("report_metadata_status")
        if status == "invalid":
            if metadata == "empty":
                out.append("пустой отчёт")
            else:
                detail = ", ".join(rec.get("contract_reasons") or [])
                out.append("некорректный формат" + (f" ({detail})" if detail else ""))
        elif status == "unavailable":
            out.append("отчёт недоступен" if metadata != "empty" else "пустой отчёт")
    if rec.get("stop_reason") == "max_tokens":
        out.append("обрыв по токенам")
    if cap and turns >= cap:
        out.append(f"лимит ходов {turns}/{cap} (предупреждение бюджета)")
    elif cap and turns >= cap * NEAR_CAP:
        out.append(f"почти лимит ходов {turns}/{cap} (предупреждение бюджета)")
    return out


def format_summary(records):
    """Aggregate validation state, keeping old log rows visibly unknown."""
    states = {}
    for rec in records:
        state = rec.get("contract_status") if "contract_status" in rec else "unknown/legacy"
        states[state or "unknown"] = states.get(state or "unknown", 0) + 1
    return " ".join(f"{state}:{count}" for state, count in sorted(states.items()))


MIN_MEANINGFUL = 15  # fewer runs than this and a percentage is noise, not a signal


def contract_agent_names():
    """The agents that actually have a role contract, straight off the disk."""
    return {path.stem for path in AGENTS_DIR.glob("*.md")}


def prompts_edited_at():
    """Newest mtime among contract-bearing agent files — the format-discipline boundary.

    mtime is a weak proxy: restoring or copying a file moves it without a real
    prompt edit behind it. Good enough here because nothing else records "when
    was this agent's prompt last changed".
    """
    times = [path.stat().st_mtime for path in AGENTS_DIR.glob("*.md")]
    if not times:
        return None
    return datetime.fromtimestamp(max(times), tz=timezone.utc)


def contract_bearing(records, names):
    """Runs from a contract-bearing agent with a real, validated contract_status."""
    return [
        rec
        for rec in records
        if rec.get("agent_type") in names
        and (rec.get("schema") or 0) >= 2
        and "contract_status" in rec
        and rec.get("contract_status") != "exempt"
    ]


def valid_share(group):
    total = len(group)
    valid = sum(1 for rec in group if rec.get("contract_status") == "valid")
    return valid, total


def format_side(label, valid, total):
    if not total:
        return f"  {label}: нет прогонов"
    pct = 100 * valid / total
    note = "" if total >= MIN_MEANINGFUL else " (мало данных, вывод не показателен)"
    return f"  {label}: {valid}/{total} валидных ({pct:.0f}%){note}"


def format_discipline_lines(runs):
    """До/после правки промптов агентов — доля валидных отчётов по семи контрактным ролям."""
    boundary = prompts_edited_at()
    if boundary is None:
        return ["ФОРМАТ ДО/ПОСЛЕ ПРАВКИ ПРОМПТОВ — файлы агентов не найдены, сравнение недоступно"]

    bearing = contract_bearing(runs, contract_agent_names())
    before, after = [], []
    for rec in bearing:
        ts = parse_ts(rec)
        if ts is None:
            continue  # can't place it on either side of the boundary
        # A run obeys whatever prompt was on disk when it *started*, not when
        # it finished — edits made mid-run (or right after) never reach the
        # already-spawned agent. `timestamp` is logged at completion, so back
        # out the start via duration_s. Without a duration we can't place the
        # start reliably, so default to "before": an unmeasured run must not
        # be credited to the post-edit period it can't be proven to belong to.
        duration_s = rec.get("duration_s")
        start = ts - timedelta(seconds=duration_s) if duration_s else ts
        (before if duration_s is None or start < boundary else after).append(rec)

    stamp = boundary.strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"ФОРМАТ ДО/ПОСЛЕ ПРАВКИ ПРОМПТОВ (граница {stamp}, по мтайм файлов agents/)"]
    lines.append(format_side("до правки", *valid_share(before)))
    lines.append(format_side("после правки", *valid_share(after)))

    invalid_after = [rec for rec in after if rec.get("contract_status") == "invalid"]
    if not after:
        pass
    elif not invalid_after:
        lines.append("  нарушений после правки нет")
    else:
        by_reason, by_agent = {}, {}
        for rec in invalid_after:
            agent = rec.get("agent_type") or "?"
            by_agent[agent] = by_agent.get(agent, 0) + 1
            for reason in rec.get("contract_reasons") or ["(без причины)"]:
                by_reason[reason] = by_reason.get(reason, 0) + 1
        lines.append(
            "  нарушения после правки по причинам: "
            + ", ".join(f"{k}:{v}" for k, v in sorted(by_reason.items(), key=lambda kv: -kv[1]))
        )
        lines.append(
            "  нарушения после правки по агентам: "
            + ", ".join(f"{k}:{v}" for k, v in sorted(by_agent.items(), key=lambda kv: -kv[1]))
        )
    return lines


def main():
    ap = argparse.ArgumentParser(description="Сводка по прогонам саб-агентов")
    ap.add_argument("--days", type=int, default=0, help="только за последние N дней")
    ap.add_argument("--runs", type=int, default=12, help="сколько подозрительных прогонов показать")
    ap.add_argument("--log", type=Path, default=RUN_LOG, help="путь к subagent-runs.jsonl")
    args = ap.parse_args()

    runs = recent(read_jsonl(args.log), args.days)
    caps = agent_caps()
    window = f"за {args.days} дн." if args.days else "за всё время"

    if not runs:
        print(f"Прогонов не найдено ({args.log}, {window}).")
        return

    print(f"ПРОГОНЫ {window} — {len(runs)} шт., лог {args.log}\n")
    print(f"{'агент':<22}{'шт':>4}{'ходы med/max':>14}{'лимит':>7}{'токены':>9}  вердикты")

    by_agent = {}
    for rec in runs:
        by_agent.setdefault(rec.get("agent_type") or "?", []).append(rec)

    for agent, group in sorted(by_agent.items(), key=lambda kv: -len(kv[1])):
        turns = [r.get("turns") or 0 for r in group]
        verdicts = {}
        for rec in group:
            key = rec.get("verdict") or "—"
            verdicts[key] = verdicts.get(key, 0) + 1
        cap = caps.get(agent)
        summary = " ".join(f"{k}:{v}" for k, v in sorted(verdicts.items()))
        print(
            f"{agent:<22}{len(group):>4}"
            f"{f'{int(statistics.median(turns))}/{max(turns)}':>14}"
            f"{cap or '—':>7}"
            f"{human(sum(tokens(r) for r in group)):>9}  {summary}"
        )

    print(f"\nФОРМАТ ОТЧЁТОВ — {format_summary(runs)}")
    for line in format_discipline_lines(runs):
        print(line)

    suspicious = [(rec, flags(rec, caps)) for rec in runs]
    suspicious = [(rec, f) for rec, f in suspicious if f]
    print(f"\nПОДОЗРИТЕЛЬНЫЕ — {len(suspicious)} из {len(runs)}")
    if not suspicious:
        print("  по доступным данным предупреждений нет")
    for rec, why in suspicious[-args.runs:]:
        ts = parse_ts(rec)
        stamp = ts.strftime("%m-%d %H:%M") if ts else "??"
        print(f"  {stamp}  {rec.get('agent_type', '?'):<20} {'; '.join(why)}")

    gate = recent(read_jsonl(GATE_LOG), args.days)
    denied = [r for r in gate if r.get("decision") == "deny"]
    other = [r for r in gate if r.get("decision") != "deny"]
    if gate:
        print(f"\nГЕЙТ ОРКЕСТРАЦИИ — отказов {len(denied)}", end="")
        if denied:
            by_type = {}
            for rec in denied:
                key = rec.get("agent_type") or "?"
                by_type[key] = by_type.get(key, 0) + 1
            print(" (" + ", ".join(f"{k}:{v}" for k, v in sorted(by_type.items())) + ")", end="")
        print()
        for rec in other:
            print(f"  ! {rec.get('decision')}: {str(rec.get('reason'))[:70]}")

    total = sum(tokens(r) for r in runs)
    seconds = sum(r.get("duration_s") or 0 for r in runs)
    print(f"\nИТОГО  токенов {human(total)}   время {seconds / 60:.0f} мин")
    print("Токены включают чтение кэша — оно дешевле свежего ввода, здесь не разделено.")


if __name__ == "__main__":
    main()
