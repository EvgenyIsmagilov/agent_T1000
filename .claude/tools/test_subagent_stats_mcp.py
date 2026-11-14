"""Тесты MCP-сервера сводки по прогонам саб-агентов."""
from __future__ import annotations

import importlib.util
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).with_name("subagent_stats_mcp.py")
spec = importlib.util.spec_from_file_location("subagent_stats_mcp", MODULE_PATH)
mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcp)


def rec(hours_ago: float, agent: str, verdict: str | None, slice_id: str | None, **extra) -> dict:
  ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
  base = {
    "timestamp": ts.isoformat(), "schema": 6, "agent_type": agent, "model": "claude-haiku-5-5",
    "verdict": verdict, "slice_id": slice_id, "tier": None, "stop_reason": "tool_use",
    "input_tokens": 10, "output_tokens": 20, "cache_creation_tokens": 30, "cache_read_tokens": 40,
    "duration_s": 10.0,
  }
  return {**base, **extra}


@pytest.fixture
def log(tmp_path: Path) -> Path:
  rows = [
    rec(1, "test-runner", "PASS", "s1"),
    rec(2, "code-writer", "FAIL", "s1", duration_s=30.0),
    rec(3, "code-writer", "DONE", "s2", model="claude-sonnet-5-5"),
    rec(4, "reviewer", "PASS", "s2"),
    rec(100, "code-writer", "PASS", "old"),
    {"agent_type": "code-writer"},  # старая схема: почти нет ключей, нет timestamp
  ]
  lines = [json.dumps(r) for r in rows]
  lines.insert(2, '{"timestamp": "2026-11-13T13:3')  # оборванная строка
  path = tmp_path / "runs.jsonl"
  path.write_text("\n".join(lines) + "\n")
  return path


def test_summary_happy_path(log: Path) -> None:
  s = mcp.build_summary(log, 24)
  assert s["records_total"] == 6  # битая строка не считается, запись без timestamp считается
  assert s["records_in_window"] == 4
  assert s["totals"]["runs"] == 4
  assert (s["totals"]["pass"], s["totals"]["fail"], s["totals"]["other"]) == (3, 1, 0)  # DONE считается pass
  assert s["totals"]["input_tokens"] == 40
  assert s["totals"]["duration_s_p50"] == 10.0
  assert s["totals"]["duration_s_p95"] == 30.0
  top = s["by_agent_type"][0]
  assert (top["agent_type"], top["runs"], top["fail"], top["tokens_total"]) == ("code-writer", 2, 1, 200)
  assert [m["model"] for m in s["by_model"]] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
  assert s["discipline"] == {"slices_total": 2, "slices_without_test_runner": 1, "slices_without_reviewer": 1}
  assert s["recent"][0]["agent_type"] == "test-runner"  # самая новая запись первой
  assert s["truncated"] is False


def test_window_and_agent_filter(log: Path) -> None:
  assert mcp.build_summary(log, 200)["records_in_window"] == 5
  s = mcp.build_summary(log, 24, "reviewer")
  assert s["records_in_window"] == 1
  assert s["by_agent_type"][0]["agent_type"] == "reviewer"


def test_record_with_missing_keys_and_empty_log(tmp_path: Path) -> None:
  path = tmp_path / "r.jsonl"
  path.write_text(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat()}) + "\n")
  s = mcp.build_summary(path, 1)
  assert s["records_in_window"] == 1
  assert s["totals"]["other"] == 1
  assert s["recent"][0]["tokens_total"] == 0
  assert mcp.build_summary(tmp_path / "missing.jsonl")["totals"]["duration_s_p95"] == 0


def test_truncation_flag(tmp_path: Path) -> None:
  path = tmp_path / "big.jsonl"
  path.write_text("\n".join(json.dumps(rec(0.1, "a", "PASS", "x" * 20000)) for _ in range(50)) + "\n")
  s = mcp.build_summary(path, 1)
  assert s["truncated"] is True
  assert 0 < len(s["recent"]) < 50
  assert len(json.dumps(s, separators=(",", ":")).encode()) <= mcp.MAX_PAYLOAD_BYTES


def test_handler_initialize_and_notification() -> None:
  req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}
  resp = mcp.handle_request(req)
  assert resp["result"]["protocolVersion"] == "2025-06-18"
  assert resp["result"]["serverInfo"]["name"] == "subagent-stats"
  assert mcp.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_handler_tools_list_and_unknown_method() -> None:
  tools = mcp.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
  assert [t["name"] for t in tools] == ["get_runs_summary"]
  assert tools[0]["annotations"]["readOnlyHint"] is True
  assert mcp.handle_request({"jsonrpc": "2.0", "id": 3, "method": "nope"})["error"]["code"] == -32601


def test_handler_tools_call(log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv("SUBAGENT_RUNS_LOG", str(log))
  req = {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "get_runs_summary", "arguments": {"since_hours": 24}}}
  result = mcp.handle_request(req)["result"]
  assert result["isError"] is False
  payload = json.loads(result["content"][0]["text"])
  assert payload["records_in_window"] == 4
  bad = {**req, "params": {"name": "get_runs_summary", "arguments": {"since_hours": 99999}}}
  assert mcp.handle_request(bad)["error"]["code"] == -32602


def write_log(path: Path, rows: list[dict]) -> Path:
  path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
  return path


def test_verdict_mapping_and_histogram(tmp_path: Path) -> None:
  rows = [
    rec(1, "reviewer", "APPROVE", None),
    rec(1, "reviewer", " request_changes ", None),
    rec(1, "code-writer", "PARTIAL", None),
    rec(1, "code-writer", None, None),
    rec(1, "code-writer", "PARTIAL", None),
  ]
  s = mcp.build_summary(write_log(tmp_path / "v.jsonl", rows), 24)
  assert (s["totals"]["pass"], s["totals"]["fail"], s["totals"]["other"]) == (1, 1, 3)
  assert s["verdicts"] == {"PARTIAL": 2, "APPROVE": 1, " request_changes ": 1, "(none)": 1}
  assert list(s["verdicts"])[0] == "PARTIAL"
  by = {a["agent_type"]: a for a in s["by_agent_type"]}
  assert (by["reviewer"]["pass"], by["reviewer"]["fail"]) == (1, 1)
  assert (by["code-writer"]["pass"], by["code-writer"]["fail"]) == (0, 0)


def test_discipline_ignores_agent_type_filter(tmp_path: Path) -> None:
  rows = [rec(1, a, "PASS", "s1") for a in ("code-writer", "test-runner", "reviewer")]
  path = write_log(tmp_path / "d.jsonl", rows)
  zeros = {"slices_total": 1, "slices_without_test_runner": 0, "slices_without_reviewer": 0}
  assert mcp.build_summary(path, 24)["discipline"] == zeros
  assert mcp.build_summary(path, 24, "code-writer")["discipline"] == zeros


def test_non_finite_numbers_give_strict_json(tmp_path: Path) -> None:
  now = datetime.now(timezone.utc).isoformat()
  path = tmp_path / "n.jsonl"
  path.write_text('{"timestamp": "%s", "input_tokens": 1e400, "duration_s": NaN}\n' % now)
  text = json.dumps(mcp.build_summary(path, 1), allow_nan=False)  # NaN/Infinity в сводке дали бы ValueError
  def reject(constant: str) -> None:
    raise ValueError(constant)
  parsed = json.loads(text, parse_constant=reject)
  assert parsed["totals"]["input_tokens"] == 0
  assert parsed["totals"]["duration_s_p50"] == 0


def test_log_exists_flag(tmp_path: Path) -> None:
  assert mcp.build_summary(tmp_path / "missing.jsonl")["log_exists"] is False
  assert mcp.build_summary(write_log(tmp_path / "e.jsonl", []))["log_exists"] is True


def test_main_survives_deeply_nested_line(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
  nested = "[" * 2000 + "]" * 2000
  tools_list = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/list"})
  monkeypatch.setattr("sys.stdin", io.StringIO(nested + "\n" + tools_list + "\n"))
  assert mcp.main() == 0
  first, second = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
  assert first["error"]["code"] == -32700
  assert second["id"] == 9 and second["result"]["tools"]


@pytest.fixture
def agents_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
  d = tmp_path / "agents"
  d.mkdir()
  (d / "code-writer.md").write_text("---\nname: code-writer\nmaxTurns: 10\n---\nmaxTurns: 99\n")
  (d / "reviewer.md").write_text("---\nname: reviewer\n---\nbody\n")  # без maxTurns: ничего не даёт
  monkeypatch.setenv("SUBAGENT_AGENTS_DIR", str(d))
  return d


def diag_rows() -> list[dict]:
  return [
    rec(1, "code-writer", "FAIL", None, turns=9, effort="max", contract_status="invalid",
        contract_reasons=["nonstandard_status_line", "missing_sections"], verdict_status="lost",
        stop_reason="refusal"),
    rec(1, "code-writer", "DONE", None, turns=3, effort={"level": "max"}, model="m2"),
    rec(1, "code-writer", "PASS", None, schema=4, turns=500, effort=None),  # старая схема
    rec(1, "reviewer", "APPROVE", None, turns=7, previous_failure_class="spec",
        report_metadata_status="unavailable", input_tokens=None, output_tokens=None,
        cache_creation_tokens=None, cache_read_tokens=None),
  ]


def test_turn_caps_from_frontmatter_only(agents_dir: Path) -> None:
  assert mcp.load_turn_caps() == {"code-writer": 10}  # maxTurns в теле файла не считается


def test_tokens_and_fail_rate(tmp_path: Path, agents_dir: Path) -> None:
  s = mcp.build_summary(write_log(tmp_path / "r.jsonl", diag_rows()), 24)
  assert s["tokens"]["by_kind"] == {"input": 30, "output": 60, "cache_creation": 90, "cache_read": 120, "total": 300}
  assert s["tokens"]["per_run"] == {"p50": 100, "p95": 100, "max": 100}
  top = s["tokens"]["by_agent_type"][0]
  assert (top["agent_type"], top["tokens_total"], top["share"]) == ("code-writer", 300, 1.0)
  assert [m["model"] for m in s["tokens"]["by_model"]] == ["claude-haiku-5-5", "m2"]
  assert s["fail_rate"] == {"pass": 3, "fail": 1, "rate": 0.25}
  by_type = {a["agent_type"]: a["fail_rate"] for a in s["by_agent_type"]}
  assert by_type == {"code-writer": 1 / 3, "reviewer": 0.0}


def test_turns_exclude_old_schema(tmp_path: Path, agents_dir: Path) -> None:
  t = mcp.build_summary(write_log(tmp_path / "r.jsonl", diag_rows()), 24)["turns"]
  assert (t["records_considered"], t["records_excluded_old_schema"]) == (3, 1)
  assert t["per_run"] == {"p50": 7, "p95": 9, "max": 9}  # 500 из схемы 4 не попал
  cw = next(a for a in t["by_agent_type"] if a["agent_type"] == "code-writer")
  assert cw == {"agent_type": "code-writer", "runs": 2, "p50": 3, "max": 9, "cap": 10, "at_cap": 1, "over_cap": 0}
  rv = next(a for a in t["by_agent_type"] if a["agent_type"] == "reviewer")
  assert (rv["cap"], rv["at_cap"], rv["over_cap"]) == (None, None, None)


def test_trouble_counts_run_once_and_reasons_overlap(tmp_path: Path, agents_dir: Path) -> None:
  s = mcp.build_summary(write_log(tmp_path / "r.jsonl", diag_rows()), 24)
  assert s["trouble"] == {"runs": 2, "rate": 0.5}  # первая запись несёт четыре сигнала, считается один раз
  reasons = {r["reason"]: r for r in s["fail_reasons"]}
  for key in ("review_rejected", "refused", "verdict_lost", "report_malformed", "at_turn_limit",
              "contract_nonstandard_status_line", "contract_missing_sections"):
    assert reasons[key]["count"] == 1
  assert reasons["retry_spec"]["label"] == "Retry after a spec failure"
  assert reasons["contract_missing_sections"]["label"] == "Report: missing sections"
  assert "truncated" not in reasons  # нулевые сигналы отброшены
  assert reasons["review_rejected"]["share"] == 0.25
  assert reasons["at_turn_limit"]["turns"] == {"p50": 9, "max": 9, "cap_hint": 10, "at_cap": 1, "over_cap": 0}
  counts = [r["count"] for r in s["fail_reasons"]]
  assert counts == sorted(counts, reverse=True)


def test_malformed_effort_does_not_raise(tmp_path: Path, agents_dir: Path) -> None:
  rows = [rec(1, "a", "FAIL", None, effort={"level": "max"}), rec(1, "a", "FAIL", None, effort="low"),
          rec(1, "a", "FAIL", None)]
  reason = mcp.build_summary(write_log(tmp_path / "r.jsonl", rows), 24)["fail_reasons"][0]
  assert {e["value"] for e in reason["by_effort"]} == {"(malformed)", "low", "(none)"}
  json.dumps(reason)


def test_no_verdicts_and_empty_window(tmp_path: Path, agents_dir: Path) -> None:
  s = mcp.build_summary(write_log(tmp_path / "r.jsonl", [rec(1, "a", None, None)]), 24)
  assert s["fail_rate"]["rate"] is None
  assert s["trouble"] == {"runs": 0, "rate": 0.0}
  empty = mcp.build_summary(tmp_path / "missing.jsonl", 24)
  assert (empty["fail_rate"]["rate"], empty["trouble"]["rate"], empty["fail_reasons"]) == (None, None, [])
  assert empty["turns"]["per_run"]["max"] is None


def test_missing_agents_dir_degrades(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv("SUBAGENT_AGENTS_DIR", str(tmp_path / "nope"))
  s = mcp.build_summary(write_log(tmp_path / "r.jsonl", diag_rows()), 24)
  assert s["turn_caps"] == {}
  assert all(a["cap"] is None and a["at_cap"] is None for a in s["turns"]["by_agent_type"])
  assert "at_turn_limit" not in {r["reason"] for r in s["fail_reasons"]}
  assert all(r["turns"]["cap_hint"] is None and r["turns"]["over_cap"] is None for r in s["fail_reasons"])


def test_new_fields_survive_truncation_and_handler(tmp_path: Path, agents_dir: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
  rows = [rec(0.1, "a", "FAIL", "x" * 20000, turns=5) for _ in range(50)]
  path = write_log(tmp_path / "big.jsonl", rows)
  s = mcp.build_summary(path, 1)
  assert s["truncated"] is True and 0 < len(s["recent"]) < 50
  assert s["fail_reasons"][0]["count"] == 50  # режется recent, а не новые поля
  assert len(json.dumps(s, separators=(",", ":")).encode()) <= mcp.MAX_PAYLOAD_BYTES
  monkeypatch.setenv("SUBAGENT_RUNS_LOG", str(path))
  req = {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
         "params": {"name": "get_runs_summary", "arguments": {"since_hours": 1}}}
  payload = json.loads(mcp.handle_request(req)["result"]["content"][0]["text"])
  assert {"tokens", "turns", "fail_rate", "trouble", "fail_reasons", "turn_caps"} <= payload.keys()


def test_non_scalar_stop_reason_does_not_raise(tmp_path: Path, agents_dir: Path) -> None:
  rows = [rec(1, "a", "DONE", None, stop_reason=["max_tokens"]), rec(1, "a", "DONE", None, stop_reason={"x": 1})]
  s = mcp.build_summary(write_log(tmp_path / "r.jsonl", rows), 24)
  assert s["trouble"]["runs"] == 0


def test_contract_family_fills_free_slots(tmp_path: Path, agents_dir: Path) -> None:
  # reason_i встречается (10 - i) раз: 10 разных причин, все больше, чем у retry_env
  rows = [rec(1, "a", "DONE", None, contract_reasons=[f"r{i}"]) for i in range(10) for _ in range(10 - i)]
  rows.append(rec(1, "a", "DONE", None, previous_failure_class="env"))
  reasons = [r["reason"] for r in mcp.build_summary(write_log(tmp_path / "r.jsonl", rows), 24)["fail_reasons"]]
  assert "contract_r8" in reasons and "contract_r9" in reasons  # 9-й и 10-й по частоте не отброшены
  assert reasons.index("contract_r9") > reasons.index("contract_r0")


def test_contract_floor_survives_global_cap(tmp_path: Path, agents_dir: Path) -> None:
  rows = [rec(1, "a", "DONE", None, previous_failure_class=f"c{i:02d}") for i in range(30)]
  rows += [rec(1, "a", "DONE", None, contract_reasons=[f"r{i}"]) for i in range(10)]
  reasons = [r["reason"] for r in mcp.build_summary(write_log(tmp_path / "r.jsonl", rows), 24)["fail_reasons"]]
  assert len(reasons) == mcp.FAIL_REASONS_LIMIT
  assert sum(r.startswith("contract_") for r in reasons) >= mcp.CONTRACT_REASONS_FLOOR


def test_unreadable_agent_file_keeps_other_caps(tmp_path: Path, agents_dir: Path) -> None:
  (agents_dir / "broken.md").mkdir()
  assert mcp.load_turn_caps() == {"code-writer": 10}


def test_signal_turns_ignore_old_schema(tmp_path: Path, agents_dir: Path) -> None:
  rows = [rec(1, "code-writer", "FAIL", None, turns=4), rec(1, "code-writer", "FAIL", None, turns=9),
          rec(1, "code-writer", "FAIL", None, turns=900, schema=4),
          rec(1, "code-writer", "FAIL", None, turns=900, schema=2)]
  reason = mcp.build_summary(write_log(tmp_path / "r.jsonl", rows), 24)["fail_reasons"][0]
  assert reason["reason"] == "review_rejected" and reason["count"] == 4
  assert reason["turns"]["p50"] == 4 and reason["turns"]["max"] == 9
  assert (reason["turns"]["at_cap"], reason["turns"]["over_cap"]) == (1, 0)


def test_signals_total_and_truncation_flag(tmp_path: Path, agents_dir: Path) -> None:
  many = [rec(1, "a", "DONE", None, previous_failure_class=f"c{i:02d}") for i in range(30)]
  s = mcp.build_summary(write_log(tmp_path / "many.jsonl", many), 24)
  assert (s["signals_total"], s["fail_reasons_truncated"], len(s["fail_reasons"])) == (30, True, 25)
  few = [rec(1, "a", "FAIL", None), rec(1, "a", "DONE", None, previous_failure_class="env")]
  s = mcp.build_summary(write_log(tmp_path / "few.jsonl", few), 24)
  assert s["signals_total"] == len(s["fail_reasons"]) == 2
  assert s["fail_reasons_truncated"] is False


def test_contract_floor_never_exceeds_limit(tmp_path: Path, agents_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(mcp, "CONTRACT_REASONS_FLOOR", 30)
  rows = [rec(1, "a", "DONE", None, contract_reasons=[f"r{i:02d}"]) for i in range(30)]
  s = mcp.build_summary(write_log(tmp_path / "r.jsonl", rows), 24)
  assert len(s["fail_reasons"]) == mcp.FAIL_REASONS_LIMIT
