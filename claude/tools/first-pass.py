#!/usr/bin/env python3
"""Доля задач, закрытых с первого прогона — по журналу субагентов.

`log-subagent.py` (схема 4 и выше) пишет в каждую строку `slice_id` — имя
задачи, которое оркестратор ставит в бриф. Это связывает прогон исполнителя,
проверку и ревью одной задачи, и позволяет спросить то, что раньше спросить
было нельзя: сколько задач закрылось без переделок, и по-разному ли это
выглядит на разных тирах.

Успех определяется чужими вердиктами — `test-runner` и ревьюера, — а не тем,
что исполнитель написал о себе. Строки старой схемы `slice_id` не несут и в
расчёт войти не могут; их число печатается рядом с результатом, вместе с
прочими пробелами, чтобы процент нельзя было прочитать достовернее, чем он
есть.

Только чтение, только стандартная библиотека. Запуск:
    python3 ~/.claude/tools/first-pass.py [--days N] [--slices N] [--log PATH]
"""
import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

RUN_LOG = Path(os.environ.get("CLAUDE_SUBAGENT_LOG", Path.home() / ".claude" / "logs" / "subagent-runs.jsonl"))

IMPLEMENTERS = ("code-writer-t1", "code-writer-t2", "code-writer-t3")
VALIDATORS = ("test-runner",)
REVIEWERS = ("code-reviewer", "sql-data-reviewer")
MIN_SCHEMA = 4  # раньше slice_id не писался


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
            continue
    return out


def parse_ts(rec):
    raw = rec.get("timestamp")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def recent(rows, days):
    if not days:
        return rows
    edge = datetime.now(timezone.utc) - timedelta(days=days)
    return [r for r in rows if (parse_ts(r) or edge) >= edge]


def tier_of(rec):
    """Тир из брифа; если его нет — по модели+effort, как запасной вариант.

    opus не пишет код с введения трёх тиров — старые строки с opus помечаются
    отдельно, а не как T2/T3, чтобы не путать легаси-роутинг с текущим.
    """
    if rec.get("tier"):
        return rec["tier"]
    model = rec.get("model") or ""
    effort = rec.get("effort") or ""
    if "haiku" in model:
        return "T1"
    if "sonnet" in model:
        return "T3" if effort == "max" else "T2"
    if "opus" in model:
        return "?(legacy-opus)"
    return "?"


def judge(runs):
    """Исход задачи: ok / retried / unknown, плюс причина для unknown.

    Ревью необязательно: навык требует его не для всякой правки. А вот без
    проверки исход неизвестен — такую задачу нельзя записать ни в успех, ни в
    провал, иначе процент начнёт выдумывать.
    """
    writers = [r for r in runs if r.get("agent_type") in IMPLEMENTERS]
    tests = [r for r in runs if r.get("agent_type") in VALIDATORS]
    reviews = [r for r in runs if r.get("agent_type") in REVIEWERS]

    if not tests:
        return "unknown", "не проверялась"
    if any(r.get("verdict_status") == "lost" for r in runs):
        return "unknown", "вердикт потерян"

    first_test = tests[0].get("verdict")
    last_review = reviews[-1].get("verdict") if reviews else None
    clean = len(writers) == 1 and first_test == "PASS" and last_review in (None, "APPROVE")
    return ("ok", "") if clean else ("retried", "")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30, help="окно в днях (0 — весь лог)")
    ap.add_argument("--slices", type=int, default=10, help="сколько переделанных задач показать")
    ap.add_argument("--log", type=Path, default=RUN_LOG)
    args = ap.parse_args()

    rows = recent(read_jsonl(args.log), args.days)
    window = "за всё время" if not args.days else f"за {args.days} дн."
    print(f"ПЕРВЫЙ ПРОГОН {window} — {len(rows)} строк, лог {args.log}\n")
    if not rows:
        print("  лог пуст")
        return

    legacy = [r for r in rows if (r.get("schema") or 1) < MIN_SCHEMA]
    modern = [r for r in rows if (r.get("schema") or 1) >= MIN_SCHEMA]
    unnamed = [r for r in modern if not r.get("slice_id")]

    slices = defaultdict(list)
    for rec in modern:
        if rec.get("slice_id"):
            slices[(rec.get("session_id"), rec["slice_id"])].append(rec)

    judged, unknown = {}, {}
    for key, runs in slices.items():
        if not any(r.get("agent_type") in IMPLEMENTERS for r in runs):
            continue  # без исполнителя это не задача, а побочная работа
        outcome, why = judge(runs)
        (unknown if outcome == "unknown" else judged)[key] = (outcome, why, runs)

    if not judged:
        print("  ни одной задачи с исходом: нужен прогон исполнителя и проверка\n")
    else:
        ok = sum(1 for o, _, _ in judged.values() if o == "ok")
        total = len(judged)
        print(f"задач с известным исходом: {total}")
        print(f"с первого раза:            {ok}  ({ok / total:.0%})\n")

        by_tier = Counter()
        for _, (outcome, _, runs) in judged.items():
            writers = [r for r in runs if r.get("agent_type") in IMPLEMENTERS]
            tier = tier_of(writers[0])
            by_tier[(tier, "всего")] += 1
            if outcome == "ok":
                by_tier[(tier, "ok")] += 1
        for tier in sorted({t for t, _ in by_tier}):
            total_t = by_tier[(tier, "всего")]
            ok_t = by_tier[(tier, "ok")]
            print(f"  {tier}: задач {total_t:>3}, с первого раза {ok_t:>3}  ({ok_t / total_t:.0%})")
        print("\n  Тиры берут разную работу по построению: T1 — тривиальное, T3 —")
        print("  критичное и сложное. Сравнивать их проценты между собой нельзя.")

    retried = [(k, v) for k, v in judged.items() if v[0] == "retried"]
    if retried:
        print(f"\nНЕ С ПЕРВОГО РАЗА — {len(retried)}")
        for (_, sid), (_, _, runs) in retried[-args.slices:]:
            writers = [r for r in runs if r.get("agent_type") in IMPLEMENTERS]
            tiers = " ".join(dict.fromkeys(tier_of(r) for r in writers))
            classes = [r.get("previous_failure_class") for r in runs if r.get("previous_failure_class")]
            tail = f"  причины: {', '.join(dict.fromkeys(classes))}" if classes else "  причины не указаны"
            print(f"  {sid:<34} заходов {len(writers)}  тиры {tiers}{tail}")

    classes = Counter(r["previous_failure_class"] for r in modern if r.get("previous_failure_class"))
    if classes:
        print("\nПРИЧИНЫ ПЕРЕДЕЛОК — " + ", ".join(f"{k}:{v}" for k, v in classes.most_common()))
        print("  тир поднимает только executor; spec, env и slicing — не вина модели")

    print("\nДОСТОВЕРНОСТЬ")
    print(f"  строк старой схемы (<{MIN_SCHEMA}): {len(legacy)} — имени задачи не несут, в расчёт не вошли")
    if modern:
        print(f"  прогонов без имени задачи: {len(unnamed)} из {len(modern)} — оркестратор не проставил slice_id")
    else:
        print("  строк новой схемы нет: измерять пока нечего")
    lost = sum(1 for r in modern if r.get("verdict_status") == "lost")
    print(f"  прогонов с потерянным вердиктом: {lost} — отчёт не разобран, исход неизвестен")
    if unknown:
        why = Counter(w for _, w, _ in unknown.values())
        print(f"  задач без исхода: {len(unknown)} (" + ", ".join(f"{k}: {v}" for k, v in why.most_common()) + ")")
    if judged and len(judged) < 50:
        print(f"\n  Выборка мала ({len(judged)}). При таком числе доверительный интервал")
        print("  порядка ±10-20 процентных пунктов: годится для тренда, не для цифры.")


if __name__ == "__main__":
    main()
