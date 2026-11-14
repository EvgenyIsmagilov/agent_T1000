#!/usr/bin/env python3
"""Локальный stdio MCP-сервер: сводка по прогонам саб-агентов из subagent-runs.jsonl.

Только стандартная библиотека. В stdout уходят только ответы JSON-RPC, диагностика идёт в stderr.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_LOG = Path.home() / ".claude" / "logs" / "subagent-runs.jsonl"
PROTOCOL_VERSIONS = ("2025-06-18",)
MAX_SINCE_HOURS = 8760
RECENT_LIMIT = 50
MAX_PAYLOAD_BYTES = 256 * 1024
# Словарь вердиктов взят из реальной гистограммы лога; PARTIAL и пустой вердикт намеренно попадают в other.
PASS_VERDICTS = frozenset({"PASS", "APPROVE", "DONE", "CONFIRMED"})
FAIL_VERDICTS = frozenset({"FAIL", "REQUEST_CHANGES", "BLOCKED"})
NO_VERDICT = "(none)"
MISSING = "(none)"
MALFORMED = "(malformed)"
# Лимит ходов у записей схем до 5 завышен примерно в 2.3 раза, их в статистику ходов не берём.
MIN_TURNS_SCHEMA = 5
NEAR_CAP_RATIO = 0.9
TROUBLE_STOP_REASONS = frozenset({"max_tokens", "refusal", "stop_sequence"})
CONTRACT_REASONS_FLOOR = 8  # столько самых частых contract_* остаются в списке при любом общем лимите
FAIL_REASONS_LIMIT = 25
MAX_TURNS_RE = re.compile(r"maxTurns:\s*(\d+)\s*$")
# Должны совпадать с tokens() в agent-stats.py.
TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens")

TOOL = {
  "name": "get_runs_summary",
  "description": "Aggregated statistics of subagent runs from the local log: totals, "
                 "per agent type, per model, token distribution, turn usage, fail rate, ranked "
                 "failure signals, recent runs and orchestration discipline. "
                 "discipline is computed over the whole window, before the agent_type filter. "
                 "Turn-derived numbers exclude records older than schema 5. "
                 "fail_reasons is capped; signals_total gives the full count of signals.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "since_hours": {"type": "integer", "default": 24, "minimum": 1, "maximum": MAX_SINCE_HOURS},
      "agent_type": {"type": "string", "description": "Exact match filter"},
    },
  },
  # watchTool кеширует инструмент и кормит наблюдателей только при явном readOnlyHint: true
  "annotations": {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
  },
}


def log_path() -> Path:
  return Path(os.environ.get("SUBAGENT_RUNS_LOG") or DEFAULT_LOG)


def read_jsonl(path: Path) -> list[dict]:
  if not path.is_file():
    print(f"subagent log not found: {path}", file=sys.stderr)
    return []
  out = []
  for line in path.read_text(errors="replace").splitlines():
    try:
      rec = json.loads(line)
    except Exception:  # RecursionError на глубокой вложенности тоже не должен ронять отчёт
      continue  # хук дописывает файл во время чтения: оборванная строка не должна ломать сводку
    if isinstance(rec, dict):
      out.append(rec)
  return out


def parse_ts(rec: dict) -> datetime | None:
  try:
    ts = datetime.fromisoformat(str(rec.get("timestamp", "")).replace("Z", "+00:00"))
  except ValueError:
    return None
  return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)  # наивное время сравнивать с aware нельзя


def num(value: object) -> float:
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    return 0
  return value if math.isfinite(value) else 0  # Infinity и NaN ломают JSON.parse в браузере


def tokens(rec: dict) -> int:
  return sum(num(rec.get(k)) for k in TOKEN_KEYS)


def percentile(values: list[float], pct: int) -> float:
  if not values:
    return 0
  ordered = sorted(values)
  return ordered[max(math.ceil(pct / 100 * len(ordered)) - 1, 0)]


def verdict_label(rec: dict) -> str:
  verdict = rec.get("verdict")
  return NO_VERDICT if verdict is None else str(verdict)


def classify(rec: dict) -> str:
  """Единственное место, где вердикт превращается в pass, fail или other."""
  label = verdict_label(rec).strip().upper()
  if label in PASS_VERDICTS:
    return "pass"
  return "fail" if label in FAIL_VERDICTS else "other"


def count_verdicts(recs: list[dict]) -> dict[str, int]:
  classes = [classify(r) for r in recs]
  return {"runs": len(recs), "pass": classes.count("pass"), "fail": classes.count("fail")}


def fail_rate(counts: dict[str, int]) -> float | None:
  decided = counts["pass"] + counts["fail"]
  return counts["fail"] / decided if decided else None


def verdict_histogram(recs: list[dict]) -> dict[str, int]:
  counts: dict[str, int] = {}
  for rec in recs:
    label = verdict_label(rec)
    counts[label] = counts.get(label, 0) + 1
  return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))


def group_by(recs: list[dict], key: str) -> list[tuple[str, list[dict]]]:
  groups: dict[str, list[dict]] = {}
  for rec in recs:
    groups.setdefault(str(rec.get(key)), []).append(rec)
  return sorted(groups.items(), key=lambda item: len(item[1]), reverse=True)


def discipline(recs: list[dict]) -> dict[str, int]:
  slices: dict[str, set[str]] = {}
  for rec in recs:
    if rec.get("slice_id") is not None:
      slices.setdefault(str(rec["slice_id"]), set()).add(str(rec.get("agent_type")))
  return {
    "slices_total": len(slices),
    "slices_without_test_runner": sum("test-runner" not in a for a in slices.values()),
    "slices_without_reviewer": sum("reviewer" not in a for a in slices.values()),
  }


def load_turn_caps() -> dict[str, int]:
  """Лимиты ходов из frontmatter агентов; нечитаемый каталог даёт пустую таблицу."""
  directory = Path(os.environ.get("SUBAGENT_AGENTS_DIR") or Path.home() / ".claude" / "agents")
  caps: dict[str, int] = {}
  try:
    files = sorted(directory.glob("*.md"))
  except OSError:
    return {}
  for file in files:
    try:
      lines = file.read_text(errors="replace").splitlines()
    except OSError:
      continue  # нечитаемый файл не должен гасить лимиты остальных
    if not lines or lines[0].strip() != "---":
      continue
    for line in lines[1:]:
      if line.strip() == "---":
        break
      match = MAX_TURNS_RE.match(line)
      if match:
        caps[file.stem] = int(match.group(1))
  return caps


def turns_of(rec: dict) -> float | None:
  """Число ходов, пригодное для сравнения; None для старых схем и записей без числа."""
  schema, turns = rec.get("schema"), rec.get("turns")
  if isinstance(schema, bool) or not isinstance(schema, int) or schema < MIN_TURNS_SCHEMA:
    return None
  if isinstance(turns, bool) or not isinstance(turns, (int, float)) or not math.isfinite(turns):
    return None
  return turns


def at_cap(turns: float, cap: int) -> bool:
  return turns >= NEAR_CAP_RATIO * cap


def share_list(recs: list[dict], key: str, total: float) -> list[dict]:
  rows = [
    {key: name, "tokens_total": sum(tokens(r) for r in group)}
    for name, group in group_by(recs, key)
  ]
  rows.sort(key=lambda row: row["tokens_total"], reverse=True)
  return [{**row, "share": row["tokens_total"] / total if total else 0} for row in rows]


def token_stats(recs: list[dict]) -> dict:
  per_run = [tokens(r) for r in recs]
  total = sum(per_run)
  return {
    "by_kind": {
      "input": sum(num(r.get("input_tokens")) for r in recs),
      "output": sum(num(r.get("output_tokens")) for r in recs),
      "cache_creation": sum(num(r.get("cache_creation_tokens")) for r in recs),
      "cache_read": sum(num(r.get("cache_read_tokens")) for r in recs),
      "total": total,
    },
    "per_run": {"p50": percentile(per_run, 50), "p95": percentile(per_run, 95), "max": max(per_run, default=0)},
    "by_agent_type": share_list(recs, "agent_type", total),
    "by_model": share_list(recs, "model", total),
  }


def turn_distribution(values: list[float]) -> dict:
  if not values:
    return {"p50": None, "p95": None, "max": None}
  return {"p50": percentile(values, 50), "p95": percentile(values, 95), "max": max(values)}


def cap_counts(pairs: list[tuple[float, int | None]]) -> tuple[int | None, int | None]:
  """(at_cap, over_cap) по ходам, у которых известен лимит; None, если лимита нет ни у одного."""
  known = [(t, cap) for t, cap in pairs if cap is not None]
  if not known:
    return None, None
  return sum(at_cap(t, cap) for t, cap in known), sum(t > cap for t, cap in known)


def turn_stats(recs: list[dict], caps: dict[str, int]) -> dict:
  usable = [(r, t) for r in recs if (t := turns_of(r)) is not None]
  by_type: dict[str, list[float]] = {}
  for r, t in usable:
    by_type.setdefault(str(r.get("agent_type")), []).append(t)
  rows = []
  for name, values in sorted(by_type.items(), key=lambda item: len(item[1]), reverse=True):
    cap = caps.get(name)
    near, over = cap_counts([(t, cap) for t in values])
    dist = turn_distribution(values)
    rows.append({"agent_type": name, "runs": len(values), "p50": dist["p50"], "max": dist["max"],
                 "cap": cap, "at_cap": near, "over_cap": over})
  dist = turn_distribution([t for _, t in usable])
  return {
    "records_considered": len(usable),
    "records_excluded_old_schema": len(recs) - len(usable),
    "per_run": {k: dist[k] for k in ("p50", "p95", "max")},
    "by_agent_type": rows,
  }


def is_troubled(rec: dict) -> bool:
  return (
    classify(rec) == "fail"
    or value_label(rec.get("stop_reason")) in TROUBLE_STOP_REASONS
    or rec.get("verdict_status") == "lost"
    or rec.get("report_metadata_status") == "unavailable"
  )


def value_label(value: object) -> str:
  if value is None:
    return MISSING
  return str(value) if isinstance(value, (str, int, float, bool)) else MALFORMED


def value_counts(recs: list[dict], key: str) -> list[dict]:
  counts: dict[str, int] = {}
  for r in recs:
    label = value_label(r.get(key))
    counts[label] = counts.get(label, 0) + 1
  return [{"value": v, "count": c} for v, c in sorted(counts.items(), key=lambda item: item[1], reverse=True)]


def reason_groups(recs: list[dict], caps: dict[str, int]) -> list[tuple[str, str, list[dict]]]:
  """Сигналы сбоя: прогон может попасть в несколько, это не разбиение."""
  def near_limit(r: dict) -> bool:
    t, cap = turns_of(r), caps.get(str(r.get("agent_type")))
    return t is not None and cap is not None and at_cap(t, cap)

  groups = [
    ("review_rejected", "Review rejected the work", [r for r in recs if classify(r) == "fail"]),
    ("truncated", "Hit the output limit", [r for r in recs if r.get("stop_reason") == "max_tokens"]),
    ("refused", "Model refused", [r for r in recs if r.get("stop_reason") == "refusal"]),
    ("stop_sequence", "Stopped on a stop sequence", [r for r in recs if r.get("stop_reason") == "stop_sequence"]),
    ("verdict_lost", "Verdict not captured", [r for r in recs if r.get("verdict_status") == "lost"]),
    ("report_unavailable", "No report returned",
     [r for r in recs if r.get("report_metadata_status") == "unavailable"]),
    ("report_malformed", "Report broke the format contract", [r for r in recs if r.get("contract_status") == "invalid"]),
    ("at_turn_limit", "Ran near its turn limit", [r for r in recs if near_limit(r)]),
  ]
  retry = sorted({r["previous_failure_class"] for r in recs if isinstance(r.get("previous_failure_class"), str)})
  groups += [
    (f"retry_{c}", f"Retry after a {c} failure", [r for r in recs if r.get("previous_failure_class") == c])
    for c in retry
  ]
  def reasons(r: dict) -> set[str]:
    raw = r.get("contract_reasons")
    return {x for x in raw if isinstance(x, str)} if isinstance(raw, list) else set()

  names = sorted({x for r in recs for x in reasons(r)})
  contract = [
    (f"contract_{n}", f"Report: {n.replace('_', ' ')}", [r for r in recs if n in reasons(r)]) for n in names
  ]
  contract.sort(key=lambda g: len(g[2]), reverse=True)
  return groups + contract


def ranked_reasons(recs: list[dict], caps: dict[str, int]) -> list[dict]:
  rows = []
  for reason, label, group in reason_groups(recs, caps):
    if not group:
      continue
    types = {str(r.get("agent_type")) for r in group}
    usable = [(t, caps.get(str(r.get("agent_type")))) for r in group if (t := turns_of(r)) is not None]
    dist = turn_distribution([t for t, _ in usable])
    near, over = cap_counts(usable)
    rows.append({
      "reason": reason,
      "label": label,
      "count": len(group),
      "share": len(group) / len(recs),
      "by_model": value_counts(group, "model"),
      "by_effort": value_counts(group, "effort"),
      "turns": {
        "p50": dist["p50"], "max": dist["max"],
        "cap_hint": caps.get(next(iter(types))) if len(types) == 1 else None,
        "at_cap": near, "over_cap": over,
      },
    })
  rows.sort(key=lambda row: row["count"], reverse=True)  # sort стабилен: при равенстве порядок групп
  return rows


def cap_reasons(rows: list[dict]) -> list[dict]:
  """Лимит режет с хвоста, но не трогает гарантированные contract_*; пол не больше самого лимита."""
  contract_idx = [i for i, r in enumerate(rows) if r["reason"].startswith("contract_")]
  keep = set(contract_idx[:min(CONTRACT_REASONS_FLOOR, FAIL_REASONS_LIMIT)])
  drop: set[int] = set()
  for i in reversed(range(len(rows))):
    if len(rows) - len(drop) <= FAIL_REASONS_LIMIT:
      break
    if i not in keep:
      drop.add(i)
  return [r for i, r in enumerate(rows) if i not in drop]


def build_summary(path: Path, since_hours: int = 24, agent_type: str | None = None) -> dict:
  records = read_jsonl(path)
  now = datetime.now(timezone.utc)
  cutoff = now - timedelta(hours=since_hours)
  windowed = []
  for rec in records:
    ts = parse_ts(rec)
    if ts is not None and ts >= cutoff:
      windowed.append((ts, rec))
  windowed.sort(key=lambda pair: pair[0], reverse=True)
  window_recs = [rec for _, rec in windowed]
  recs = [r for r in window_recs if agent_type is None or r.get("agent_type") == agent_type]
  durations = [num(r.get("duration_s")) for r in recs]
  counts = count_verdicts(recs)
  caps = load_turn_caps()
  troubled = sum(is_troubled(r) for r in recs)
  reasons = ranked_reasons(recs, caps)
  shown_reasons = cap_reasons(reasons)
  summary = {
    "generated_at": now.isoformat(),
    "window_hours": since_hours,
    "log_path": str(path),
    "log_exists": path.is_file(),
    "records_total": len(records),
    "records_in_window": len(recs),
    "totals": {
      **counts,
      "other": counts["runs"] - counts["pass"] - counts["fail"],
      **{k: sum(num(r.get(k)) for r in recs) for k in TOKEN_KEYS},
      "duration_s_p50": percentile(durations, 50),
      "duration_s_p95": percentile(durations, 95),
    },
    "verdicts": verdict_histogram(recs),
    "turn_caps": caps,
    "tokens": token_stats(recs),
    "turns": turn_stats(recs, caps),
    "fail_rate": {"pass": counts["pass"], "fail": counts["fail"], "rate": fail_rate(counts)},
    "trouble": {"runs": troubled, "rate": troubled / len(recs) if recs else None},
    "fail_reasons": shown_reasons,
    "signals_total": len(reasons),
    "fail_reasons_truncated": len(reasons) > len(shown_reasons),
    "by_agent_type": [
      {
        "agent_type": name,
        **count_verdicts(group),
        "fail_rate": fail_rate(count_verdicts(group)),
        "tokens_total": sum(tokens(r) for r in group),
        "duration_s_p50": percentile([num(r.get("duration_s")) for r in group], 50),
      }
      for name, group in group_by(recs, "agent_type")
    ],
    "by_model": [
      {"model": name, "runs": len(group), "tokens_total": sum(tokens(r) for r in group)}
      for name, group in group_by(recs, "model")
    ],
    "recent": [
      {
        "timestamp": r.get("timestamp"),
        "agent_type": r.get("agent_type"),
        "model": r.get("model"),
        "verdict": r.get("verdict"),
        "slice_id": r.get("slice_id"),
        "tier": r.get("tier"),
        "duration_s": num(r.get("duration_s")),
        "tokens_total": tokens(r),
        "stop_reason": r.get("stop_reason"),
      }
      for r in recs[:RECENT_LIMIT]
    ],
    "discipline": discipline(window_recs),
    "truncated": False,
  }
  while len(json.dumps(summary, separators=(",", ":")).encode()) > MAX_PAYLOAD_BYTES and summary["recent"]:
    summary["recent"].pop()  # recent отсортирован от новых к старым: режем старые
    summary["truncated"] = True
  return summary


def error(req_id: object, code: int, message: str) -> dict:
  return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def call_tool(params: dict) -> dict:
  if params.get("name") != TOOL["name"]:
    raise ValueError(f"unknown tool: {params.get('name')}")
  args = params.get("arguments") or {}
  since = args.get("since_hours", 24)
  agent_type = args.get("agent_type")
  if isinstance(since, bool) or not isinstance(since, int) or not 1 <= since <= MAX_SINCE_HOURS:
    raise ValueError(f"since_hours must be an integer in 1..{MAX_SINCE_HOURS}")
  if agent_type is not None and not isinstance(agent_type, str):
    raise ValueError("agent_type must be a string")
  payload = build_summary(log_path(), since, agent_type)
  return {"content": [{"type": "text", "text": json.dumps(payload, separators=(",", ":"))}], "isError": False}


def handle_request(req: object) -> dict | None:
  """Ответ на один запрос JSON-RPC; None для уведомлений (сообщений без id)."""
  if not isinstance(req, dict):
    return error(None, -32600, "Invalid Request")
  if "id" not in req:
    return None
  req_id, method = req["id"], req.get("method")
  params = req.get("params") if isinstance(req.get("params"), dict) else {}
  if method == "initialize":
    asked = params.get("protocolVersion")
    version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
    result = {
      "protocolVersion": version,
      "capabilities": {"tools": {}},
      "serverInfo": {"name": "subagent-stats", "version": "1"},
    }
  elif method == "tools/list":
    result = {"tools": [TOOL]}
  elif method == "tools/call":
    try:
      result = call_tool(params)
    except ValueError as exc:
      return error(req_id, -32602, str(exc))
  elif method == "ping":
    result = {}
  else:
    return error(req_id, -32601, f"Method not found: {method}")
  return {"jsonrpc": "2.0", "id": req_id, "result": result}


def main() -> int:
  for line in sys.stdin:
    if not line.strip():
      continue
    try:
      req = json.loads(line)
    except Exception:  # RecursionError на глубокой вложенности: отвечаем -32700 и живём дальше
      resp = error(None, -32700, "Parse error")
    else:
      try:
        resp = handle_request(req)
      except Exception as exc:  # один плохой запрос не должен убить процесс
        print(f"internal error: {exc!r}", file=sys.stderr)
        resp = error(req.get("id") if isinstance(req, dict) else None, -32603, "Internal error")
    if resp is not None:
      sys.stdout.write(json.dumps(resp, separators=(",", ":")) + "\n")
      sys.stdout.flush()
  return 0


if __name__ == "__main__":
  sys.exit(main())
