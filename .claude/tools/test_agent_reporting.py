#!/usr/bin/env python3
"""Regression tests for subagent report metadata and the read-only digest."""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


CLAUDE_DIR = Path(__file__).resolve().parent.parent
HOOK_PATH = CLAUDE_DIR / "hooks" / "log-subagent.py"
STATS_PATH = CLAUDE_DIR / "tools" / "agent-stats.py"
FIRST_PASS_PATH = CLAUDE_DIR / "tools" / "first-pass.py"
FIXTURES_DIR = CLAUDE_DIR / "tools" / "fixtures" / "reports"


def load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("log_subagent", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def load_first_pass() -> ModuleType:
    spec = importlib.util.spec_from_file_location("first_pass", FIRST_PASS_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Без этого импорт пишет байт-код в tools/__pycache__, даже если запуск идёт без -B.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


HOOK = load_hook()
FIRST_PASS = load_first_pass()


def report(heading: str, status: str, sections: list[str]) -> str:
    parts = [f"### {heading}", f"{status} — summary"]
    for section in sections:
        parts.extend((f"### {section}", "content"))
    return "\n".join(parts)


class ReportContractTests(unittest.TestCase):
    def assert_valid(
        self, agent: str, heading: str, status: str, required_sections: list[str]
    ) -> None:
        verdict, metadata, contract, reasons = HOOK.validate_report(
            agent, True, report(heading, status, required_sections)
        )
        self.assertEqual((verdict, metadata, contract, reasons), (status, "present", "valid", []))

    def test_all_role_contracts_and_conditional_states(self) -> None:
        self.assert_valid("web-researcher", "Result", "DONE", ["Answer", "Sources"])
        self.assert_valid(
            "docs-researcher", "Result", "PARTIAL",
            ["Answer", "Findings", "Recommended action", "Sources"],
        )
        self.assert_valid("code-writer-t2", "Result", "DONE", ["Changes", "Verification"])
        self.assert_valid(
            "bug-investigator", "Result", "PROBABLE",
            ["Root cause", "Evidence", "Fix direction", "Verification"],
        )
        self.assert_valid("test-runner", "Result", "PASS", ["Checks", "Next action"])
        self.assert_valid(
            "test-runner", "Result", "PARTIAL", ["Checks", "Failures", "Next action"],
        )
        self.assert_valid(
            "reviewer", "Verdict", "APPROVE",
            [
                "Review scope", "Spec coverage", "Findings", "Standards & smells",
                "Challenges performed", "Final assessment",
            ],
        )
        self.assert_valid(
            "reviewer", "Verdict", "REQUEST_CHANGES",
            [
                "Query profile", "Resource-risk summary", "Findings",
                "Strongest challenges performed", "Recommended verification", "Final assessment",
            ],
        )
        self.assert_valid(
            "planner", "Result", "DONE",
            ["Approach", "Slices", "Risks", "Acceptance criteria"],
        )

    def test_status_must_follow_leading_heading_outside_quotes_and_fences(self) -> None:
        valid = report("Result", "DONE", ["Answer", "Sources"])
        backticked = valid.replace("DONE — summary", "`DONE` — summary")
        self.assertEqual(HOOK.validate_report("web-researcher", True, backticked)[2], "valid")
        variants = [
            valid.replace("DONE — summary", "Work is DONE — summary"),
            valid.replace("DONE — summary", "> DONE — summary"),
            valid.replace("DONE — summary", "```\nDONE — summary\n```\ntext"),
            valid.replace("DONE — summary", "~~~\nDONE — summary\n~~~\ntext"),
        ]
        for value in variants:
            _, _, contract, reasons = HOOK.validate_report("web-researcher", True, value)
            self.assertEqual(contract, "invalid")
            self.assertIn("missing_or_invalid_status", reasons)

        fenced_verification = "\n".join(
            (
                "### Result", "DONE — summary", "### Changes", "content",
                "### Verification", "~~~sh", "> output.txt", "~~~",
            )
        )
        self.assertEqual(HOOK.validate_report("code-writer-t2", True, fenced_verification)[2], "valid")

    def test_fences_and_quotes_do_not_fake_sections_or_empty_evidence(self) -> None:
        base = "\n".join(("### Result", "DONE — summary", "### Answer", "answer"))
        four_backticks = base + "\n````\n```\n### Sources\nsource\n````"
        mixed_fences = base + "\n```\n~~~\n### Sources\nsource\n```"
        for value in (four_backticks, mixed_fences):
            _, _, contract, reasons = HOOK.validate_report("web-researcher", True, value)
            self.assertEqual(contract, "invalid")
            self.assertIn("missing_section:sources", reasons)

        mixed_content = "\n".join(
            (
                "### Result", "DONE — finished", "### Changes", "content",
                "```sh", "~~~ literal output", "```", "### Verification", "verified",
            )
        )
        self.assertEqual(HOOK.validate_report("code-writer-t2", True, mixed_content)[2], "valid")

        verification = "\n".join(
            (
                "### Result", "DONE — finished", "### Changes", "Updated parser.",
                "### Verification", "> pytest completed with exit code 0; 3 passed.",
            )
        )
        self.assertEqual(HOOK.validate_report("code-writer-t2", True, verification)[2], "valid")

    def test_structural_indentation_accepts_only_three_spaces(self) -> None:
        for prefix in ("", " ", "  ", "   "):
            value = report("Result", "DONE", ["Answer", "Sources"])
            value = value.replace("DONE — summary", prefix + "DONE — summary")
            self.assertEqual(HOOK.validate_report("web-researcher", True, value)[2], "valid")

        fake_source = "\n".join(
            ("### Result", "DONE — summary", "### Answer", "answer", "\t### Sources", "\texample")
        )
        _, _, contract, reasons = HOOK.validate_report("web-researcher", True, fake_source)
        self.assertEqual(contract, "invalid")
        self.assertIn("missing_section:sources", reasons)

        for prefix in ("    ", "\t"):
            indented_fence = "\n".join(
                (
                    "### Result", "DONE — summary", "### Changes", "content",
                    prefix + "```", "hidden-looking code", prefix + "```",
                    "### Verification", "verified",
                )
            )
            self.assertEqual(HOOK.validate_report("code-writer-t2", True, indented_fence)[2], "valid")

    def test_indented_status_is_not_a_verdict(self) -> None:
        base = "\n".join(("### Result", "", "{status}DONE — example", "", "### Answer", "answer", "### Sources", "source"))
        for prefix in ("", " ", "  ", "   "):
            self.assertEqual(
                HOOK.validate_report("web-researcher", True, base.format(status=prefix))[2],
                "valid",
            )
        for prefix in ("    ", "\t"):
            verdict, _, contract, reasons = HOOK.validate_report(
                "web-researcher", True, base.format(status=prefix)
            )
            self.assertIsNone(verdict)
            self.assertEqual(contract, "invalid")
            self.assertIn("missing_or_invalid_status", reasons)

    def test_missing_sections_and_metadata_are_distinct(self) -> None:
        _, metadata, contract, reasons = HOOK.validate_report("code-writer-t2", True, "### Result\nDONE")
        self.assertEqual((metadata, contract), ("present", "invalid"))
        self.assertIn("missing_section:verification", reasons)
        self.assertEqual(HOOK.validate_report("code-writer-t2", True, "   ")[1:3], ("empty", "invalid"))
        for present, value, reason in (
            (False, None, "report_missing"),
            (True, None, "report_null"),
            (True, {"text": "no"}, "report_nonstring"),
        ):
            _, metadata, contract, reasons = HOOK.validate_report("code-writer-t2", present, value)
            self.assertEqual((metadata, contract), ("unavailable", "unavailable"))
            self.assertEqual(reasons, [reason])

    def test_unknown_agent_is_exempt(self) -> None:
        verdict, metadata, contract, reasons = HOOK.validate_report("Explore", True, "plain response")
        self.assertEqual((verdict, metadata, contract, reasons), (None, "present", "exempt", []))

    def test_finding_verifier_valid_reply(self) -> None:
        reply = "\n".join(
            (
                "Verdict: REFUTED - the guard already rejects this input.",
                "Checked: ran validate() on the literal from the finding; read foo.py:42.",
                "Result: raises ValueError before reaching the code the finding cites.",
            )
        )
        verdict, metadata, contract, reasons = HOOK.validate_report("finding-verifier", True, reply)
        self.assertEqual((verdict, metadata, contract, reasons), ("REFUTED", "present", "valid", []))

    def test_finding_verifier_rejects_preamble_before_verdict(self) -> None:
        reply = "\n".join(
            (
                "Let me check that claim first.",
                "Verdict: HOLDS - the defect is real.",
                "Checked: read bar.py:10.",
                "Result: the null check is missing, confirmed by inspection.",
            )
        )
        verdict, metadata, contract, reasons = HOOK.validate_report("finding-verifier", True, reply)
        self.assertEqual(contract, "invalid")
        self.assertIn("preamble_before_verdict", reasons)
        self.assertEqual(verdict, "HOLDS")

    def test_finding_verifier_rejects_empty_labels(self) -> None:
        reply = "\n".join(
            (
                "Verdict: HOLDS - the defect is real.",
                "Checked:",
                "Result:",
            )
        )
        verdict, metadata, contract, reasons = HOOK.validate_report("finding-verifier", True, reply)
        self.assertEqual(contract, "invalid")
        self.assertIn("empty_section:checked", reasons)
        self.assertIn("empty_section:result", reasons)
        self.assertEqual(verdict, "HOLDS")


class GoldenReportFixtureTests(unittest.TestCase):
    """Every role in ROLE_CONTRACTS must have a golden valid/malformed pair.

    Regression guard for drift between an agent's own report format (the
    fixture, written by hand from the agent file) and ROLE_CONTRACTS (the
    hook's mirror of it). A role added to ROLE_CONTRACTS without fixtures
    fails here rather than going unnoticed.
    """

    # `<role>.malformed.md` varies only the preamble (see module docstring
    # below) — a validator collapsed to "some text before the heading" would
    # still pass every one of them. These fixtures each trip a different
    # reason code, and the assertion below pins the reason, not just
    # contract == "invalid", so that collapse fails here instead.
    EXTRA_MALFORMED_REASONS = {
        "code-writer-t1.malformed-missing-section.md": "missing_section:verification",
        "web-researcher.malformed-bad-status.md": "missing_or_invalid_status",
        "bug-investigator.malformed-empty-section.md": "empty_section:evidence",
        "test-runner.malformed-fail-no-failures.md": "missing_section:failures",
        "finding-verifier.malformed-bad-label.md": "missing_verdict",
    }

    def test_every_role_contract_has_matching_fixtures(self) -> None:
        for role, contract in HOOK.ROLE_CONTRACTS.items():
            with self.subTest(role=role):
                valid_path = FIXTURES_DIR / f"{role}.valid.md"
                malformed_path = FIXTURES_DIR / f"{role}.malformed.md"
                self.assertTrue(valid_path.is_file(), f"missing fixture: {valid_path}")
                self.assertTrue(malformed_path.is_file(), f"missing fixture: {malformed_path}")

                _, _, status, reasons = HOOK.validate_report(
                    role, True, valid_path.read_text()
                )
                self.assertEqual(status, "valid", f"{role}: {reasons}")

                _, _, status, reasons = HOOK.validate_report(
                    role, True, malformed_path.read_text()
                )
                self.assertEqual(status, "invalid", f"{role} malformed fixture unexpectedly valid")
                preamble_reason = "preamble_before_" + contract.get("heading", "verdict")
                self.assertIn(
                    preamble_reason, reasons, f"{role}: expected {preamble_reason}, got {reasons}"
                )

    def test_malformed_fixtures_trip_distinct_reasons(self) -> None:
        for filename, expected_reason in self.EXTRA_MALFORMED_REASONS.items():
            with self.subTest(fixture=filename):
                role = filename.split(".", 1)[0]
                path = FIXTURES_DIR / filename
                self.assertTrue(path.is_file(), f"missing fixture: {path}")
                _, _, status, reasons = HOOK.validate_report(role, True, path.read_text())
                self.assertEqual(status, "invalid", f"{filename} unexpectedly valid")
                self.assertIn(
                    expected_reason, reasons, f"{filename}: expected {expected_reason}, got {reasons}"
                )


class TranscriptStatsTests(unittest.TestCase):
    def test_repeated_message_id_counts_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            transcript = Path(temporary) / "agent-dedup.jsonl"
            # One real turn (thinking + text + tool_use, same id/usage) plus
            # one closing turn — a running-total usage on each block, as the
            # transcript actually writes it.
            records = [
                {"timestamp": "2026-01-01T00:00:00Z", "message": {"role": "user", "content": "go"}},
                {
                    "timestamp": "2026-01-01T00:00:01Z",
                    "message": {
                        "id": "msg_1", "role": "assistant", "model": "claude-sonnet-5",
                        "usage": {"input_tokens": 100, "output_tokens": 10},
                    },
                },
                {
                    "timestamp": "2026-01-01T00:00:02Z",
                    "message": {
                        "id": "msg_1", "role": "assistant", "model": "claude-sonnet-5",
                        "usage": {"input_tokens": 100, "output_tokens": 20},
                    },
                },
                {
                    "timestamp": "2026-01-01T00:00:03Z",
                    "message": {
                        "id": "msg_2", "role": "assistant", "model": "claude-sonnet-5",
                        "stop_reason": "end_turn",
                        "usage": {"input_tokens": 50, "output_tokens": 5},
                    },
                },
            ]
            transcript.write_text("".join(json.dumps(r) + "\n" for r in records))
            stats = HOOK.transcript_stats(str(transcript))
            self.assertEqual(stats["turns"], 2)
            self.assertEqual(stats["input_tokens"], 150)
            self.assertEqual(stats["output_tokens"], 25)

    def test_trailing_synthetic_record_does_not_overwrite_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            transcript = Path(temporary) / "agent-synthetic.jsonl"
            records = [
                {
                    "timestamp": "2026-01-01T00:00:01Z",
                    "message": {
                        "id": "msg_1", "role": "assistant", "model": "claude-sonnet-5",
                        "stop_reason": "end_turn",
                        "usage": {"input_tokens": 100, "output_tokens": 10},
                    },
                },
                # An API-error record: real transcripts stamp this model with
                # zero usage after the last real turn.
                {
                    "timestamp": "2026-01-01T00:00:02Z",
                    "message": {
                        "id": "msg_2", "role": "assistant", "model": "<synthetic>",
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                },
            ]
            transcript.write_text("".join(json.dumps(r) + "\n" for r in records))
            stats = HOOK.transcript_stats(str(transcript))
            self.assertEqual(stats["model"], "claude-sonnet-5")


class HookAndStatsTests(unittest.TestCase):
    def test_hook_jsonl_and_stats_distinguish_contract_from_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "runs.jsonl"
            transcript = Path(temporary) / "agent-test.jsonl"
            transcript.write_text(
                "".join(
                    json.dumps(record) + "\n"
                    for record in (
                        {
                            "timestamp": "2026-01-01T00:00:00Z",
                            "message": {"role": "user", "content": "do the task"},
                        },
                        {
                            "timestamp": "2026-01-01T00:00:05Z",
                            "message": {
                                "role": "assistant",
                                "content": "done",
                                "model": "claude-sonnet-5",
                                "stop_reason": "end_turn",
                                "usage": {
                                    "input_tokens": 10,
                                    "output_tokens": 5,
                                    "cache_creation_input_tokens": 0,
                                    "cache_read_input_tokens": 0,
                                },
                            },
                        },
                    )
                )
            )
            payload = {
                "hook_event_name": "SubagentStop",
                "agent_type": "code-writer-t2",
                "agent_transcript_path": str(transcript),
                "last_assistant_message": report("Result", "PARTIAL", ["Changes", "Verification"]),
            }
            env = os.environ | {"CLAUDE_SUBAGENT_LOG": str(log)}
            result = subprocess.run(
                [sys.executable, "-B", str(HOOK_PATH)], input=json.dumps(payload), text=True,
                capture_output=True, env=env, check=True,
            )
            self.assertEqual(result.stderr, "")
            entry = json.loads(log.read_text())
            self.assertEqual(entry["verdict"], "PARTIAL")
            self.assertEqual(entry["contract_status"], "valid")
            self.assertNotIn("last_assistant_message", entry)

            records = [
                entry | {"stop_reason": "max_tokens"},
                {"agent_type": "code-writer-t2", "turns": 1, "verdict": None},
                {
                    "agent_type": "code-writer-t2", "turns": 1, "report_chars": 0,
                    "contract_status": "invalid", "report_metadata_status": "empty",
                    "contract_reasons": ["report_empty"],
                },
                {
                    "agent_type": "code-writer-t2", "turns": 1, "report_chars": 12,
                    "contract_status": "invalid", "report_metadata_status": "present",
                    "contract_reasons": ["missing_section:verification"],
                },
                {"agent_type": "Explore", "turns": 1, "contract_status": "exempt"},
            ]
            log.write_text("".join(json.dumps(record) + "\n" for record in records))
            result = subprocess.run(
                [sys.executable, "-B", str(STATS_PATH), "--log", str(log)],
                text=True, capture_output=True, check=True,
            )
            self.assertIn("ФОРМАТ ОТЧЁТОВ", result.stdout)
            self.assertIn("unknown/legacy:1", result.stdout)
            self.assertIn("обрыв по токенам", result.stdout)
            self.assertIn("пустой отчёт", result.stdout)
            self.assertIn("некорректный формат", result.stdout)
            self.assertNotIn("без вердикта", result.stdout)

    def test_stats_treats_nonstandard_status_line_as_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "runs.jsonl"
            records = (
                {
                    "agent_type": "code-writer-t1", "report_metadata_status": "present",
                    "contract_status": "invalid", "contract_reasons": ["nonstandard_status_line"],
                },
                {
                    "agent_type": "code-writer-t2", "report_metadata_status": "present",
                    "contract_status": "invalid",
                    "contract_reasons": ["nonstandard_status_line", "missing_section:changes"],
                },
            )
            log.write_text("".join(json.dumps(record) + "\n" for record in records))
            result = subprocess.run(
                [sys.executable, "-B", str(STATS_PATH), "--log", str(log)],
                text=True, capture_output=True, check=True,
            )
            self.assertIn("ПОДОЗРИТЕЛЬНЫЕ — 1 из 2", result.stdout)
            self.assertIn(
                "нестандартная строка статуса (вердикт прочитан, предупреждение): 2",
                result.stdout,
            )
            self.assertIn("некорректный формат (missing_section:changes)", result.stdout)

    def test_stats_only_soft_rows_report_no_suspicious_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "runs.jsonl"
            records = (
                {
                    "agent_type": "code-writer-t1", "report_metadata_status": "present",
                    "contract_status": "invalid", "contract_reasons": ["nonstandard_status_line"],
                },
            )
            log.write_text("".join(json.dumps(record) + "\n" for record in records))
            result = subprocess.run(
                [sys.executable, "-B", str(STATS_PATH), "--log", str(log)],
                text=True, capture_output=True, check=True,
            )
            self.assertIn("ПОДОЗРИТЕЛЬНЫЕ — 0 из 1", result.stdout)
            self.assertIn(
                "нестандартная строка статуса (вердикт прочитан, предупреждение): 1",
                result.stdout,
            )
            self.assertIn("подозрительных прогонов нет", result.stdout)
            self.assertNotIn("предупреждений нет", result.stdout)


class BriefMetricsTests(unittest.TestCase):
    def identity(self, prompt: str) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "agent.jsonl"
            t.write_text(json.dumps({"message": {"role": "user", "content": prompt}}) + "\n")
            return HOOK.brief_identity(t)

    def test_valid_metrics(self) -> None:
        got = self.identity("slice_id: s1\ndivisible: No \u2014 one parser\nvolume: 9 files, 4200 lines")
        self.assertEqual((got["divisible"], got["volume_files"], got["volume_lines"]), ("no", 9, 4200))
        self.assertEqual(got["slice_id"], "s1")

    def test_examples_before_fields_are_ignored(self) -> None:
        fence = "```\nvolume: 9 files, 4200 lines\n```\n"
        real = "divisible: no \u2014 one parser\nvolume: 3 files, 1498 lines"
        examples = (
            "Examples:\n- `divisible: yes \u2014 two files`\n- `volume: 9 files, 4200 lines`\n",
            "Example:\n" + fence,
        )
        for ex in examples:
            got = self.identity("slice_id: s1\n" + ex + real)
            self.assertEqual((got["divisible"], got["volume_files"], got["volume_lines"]), ("no", 3, 1498), ex)

    def metrics(self, prompt: str) -> tuple[object, object, object]:
        got = self.identity(prompt)
        return got["divisible"], got["volume_files"], got["volume_lines"]

    def test_inline_triple_backtick_in_prose_is_not_a_fence(self) -> None:
        prompt = "fields inside ``` fenced blocks are ignored\ndivisible: no \u2014 x\nvolume: 3 files, 10 lines"
        self.assertEqual(self.metrics(prompt), ("no", 3, 10))

    def test_inline_backticks_do_not_flip_fence_parity(self) -> None:
        prompt = (
            "Note: use ``` for code.\nExample:\n```\ndivisible: yes - x\nvolume: 9 files, 4200 lines\n```\n"
            "divisible: no - y\nvolume: 3 files, 10 lines"
        )
        self.assertEqual(self.metrics(prompt), ("no", 3, 10))

    def test_longer_outer_fence_is_not_closed_by_shorter_run(self) -> None:
        prompt = (
            "````\n```\ndivisible: yes - x\n```\nvolume: 9 files, 4200 lines\n````\n"
            "divisible: no - y\nvolume: 3 files, 10 lines"
        )
        self.assertEqual(self.metrics(prompt), ("no", 3, 10))

    def test_tilde_fence_hides_example(self) -> None:
        prompt = "~~~\ndivisible: yes - x\nvolume: 9 files, 4200 lines\n~~~\ndivisible: no - y\nvolume: 3 files, 10 lines"
        self.assertEqual(self.metrics(prompt), ("no", 3, 10))

    def test_unclosed_fence_hides_rest(self) -> None:
        got = self.identity("```\ndivisible: no \u2014 x\nvolume: 3 files, 5 lines")
        self.assertEqual((got["divisible"], got["volume_files"], got["volume_lines"]), (None,) * 3)

    def test_many_fences_large_prompt_is_fast(self) -> None:
        import time
        block = "```\nvolume: 9 files, 4200 lines\n```\n" + "x" * 2000 + "\n"
        prompt = block * 260 + "divisible: no \u2014 one parser\nvolume: 3 files, 1498 lines"
        self.assertGreater(len(prompt), 500_000)
        start = time.perf_counter()
        got = self.identity(prompt)
        self.assertLess(time.perf_counter() - start, 1.0)
        self.assertEqual((got["divisible"], got["volume_files"], got["volume_lines"]), ("no", 3, 1498))

    def test_balanced_backtick_field_names(self) -> None:
        got = self.identity("- `divisible`: yes, three modules\n- `volume`: 9 files, 4200 lines")
        self.assertEqual((got["divisible"], got["volume_files"], got["volume_lines"]), ("yes", 9, 4200))

    def test_thousands_separators(self) -> None:
        for text in ("4,200 lines", "4 200 lines"):
            got = self.identity(f"- `divisible`: yes \u2014 two files\n- volume: 12 files, {text}")
            self.assertEqual((got["divisible"], got["volume_files"], got["volume_lines"]), ("yes", 12, 4200))

    def test_placeholders_and_garbage_give_null(self) -> None:
        for prompt in ("divisible: yes|no", "divisible: yes/no", "divisible: yesterday",
                       "volume: N files, M lines", "volume: many files", "no metrics here"):
            got = self.identity(prompt)
            self.assertEqual((got["divisible"], got["volume_files"], got["volume_lines"]), (None,) * 3, prompt)

    def test_missing_transcript_gives_null_metrics(self) -> None:
        got = HOOK.brief_identity(None)
        self.assertEqual((got["divisible"], got["volume_files"], got["volume_lines"]), (None,) * 3)

    def test_brief_re_slice_id_unchanged(self) -> None:
        spec = importlib.util.spec_from_file_location("bf", CLAUDE_DIR / "hooks" / "brief_fields.py")
        bf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bf)
        self.assertEqual(set(bf.BRIEF_RE), {"slice_id", "tier", "previous_failure_class"})
        self.assertEqual(bf.BRIEF_RE["slice_id"].search("- slice_id: abc-1\n").group(1), "abc-1")


class HandbackHookTests(unittest.TestCase):
    REMINDER = {
        "type": "user", "isMeta": True,
        "message": {"role": "user", "content": "Deliver the report by calling SubagentHandback."},
    }
    BRIEF = {"type": "user", "message": {"role": "user", "content": "brief: mention SubagentHandback"}}
    BASH = {
        "type": "assistant",
        "message": {"id": "m1", "role": "assistant", "model": "claude-sonnet-5", "stop_reason": "tool_use",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                    "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]},
    }

    @staticmethod
    def handback(text):
        return {
            "type": "assistant",
            "message": {"id": "m2", "role": "assistant", "model": "claude-sonnet-5", "stop_reason": "tool_use",
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                        "content": [{"type": "tool_use", "name": "SubagentHandback", "input": {"message": text}}]},
        }

    def run_hook(self, directory, records, extra=None):
        transcript = Path(directory) / "agent.jsonl"
        transcript.write_text("".join(json.dumps(r) + "\n" for r in records))
        payload = {"hook_event_name": "SubagentStop", "agent_type": "code-writer-t2",
                   "agent_transcript_path": str(transcript)} | (extra or {})
        env = os.environ | {"CLAUDE_SUBAGENT_LOG": str(Path(directory) / "runs.jsonl"),
                            "CLAUDE_SUBAGENT_SKIPPED_LOG": str(Path(directory) / "skipped.jsonl")}
        subprocess.run([sys.executable, "-B", str(HOOK_PATH)], input=json.dumps(payload), text=True,
                       capture_output=True, env=env, check=True)

    @staticmethod
    def rows(directory, name):
        path = Path(directory) / name
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_handback_report_is_read_from_transcript(self) -> None:
        text = report("Result", "DONE", ["Changes", "Verification"])
        with tempfile.TemporaryDirectory() as d:
            self.run_hook(d, [self.BRIEF, self.REMINDER, self.BASH, self.handback(text)])
            (row,) = self.rows(d, "runs.jsonl")
            self.assertEqual(row["report_source"], "handback")
            self.assertEqual(row["report_metadata_status"], "present")
            self.assertEqual(row["contract_status"], "valid")
            self.assertEqual(row["verdict"], "DONE")
            self.assertEqual(row["report_chars"], len(text))
            self.assertEqual(row["schema"], 7)
            self.assertIsNone(row["divisible"])
            self.assertIsNone(row["volume_files"])
            self.assertNotIn(text, json.dumps(row))

    def test_pre_handback_stop_is_skipped_then_handback_logged_once(self) -> None:
        text = report("Result", "DONE", ["Changes", "Verification"])
        with tempfile.TemporaryDirectory() as d:
            self.run_hook(d, [self.BRIEF, self.REMINDER, self.BASH], {"last_assistant_message": "plain text"})
            self.assertEqual(self.rows(d, "runs.jsonl"), [])
            (skipped,) = self.rows(d, "skipped.jsonl")
            self.assertEqual(skipped["reason"], "awaiting_handback")
            self.run_hook(d, [self.BRIEF, self.REMINDER, self.BASH, self.handback(text)])
            (row,) = self.rows(d, "runs.jsonl")
            self.assertEqual(row["report_source"], "handback")
            self.assertEqual(skipped["agent_id"], row["agent_id"])

    def test_continued_agent_ignores_previous_handback(self) -> None:
        old = report("Result", "BLOCKED", ["Changes", "Verification"])
        new = report("Result", "DONE", ["Changes", "Verification"]) + "\nextra"
        meta = {"type": "user", "isMeta": True,
                "message": {"role": "user", "content": [{"type": "text", "text": "continue"}]}}
        text_end = {"type": "assistant",
                    "message": {"id": "m3", "role": "assistant", "model": "claude-sonnet-5",
                                "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1},
                                "content": [{"type": "text", "text": "done"}]}}
        enforce = {"type": "user", "isMeta": True,
                   "message": {"role": "user", "content": "[handback-send-enforce] use SubagentHandback"}}
        new_handback = self.handback(new)
        new_handback["message"]["id"] = "m4"
        base = [self.BRIEF, self.REMINDER, self.BASH, self.handback(old), meta, text_end]
        with tempfile.TemporaryDirectory() as d:
            self.run_hook(d, base, {"last_assistant_message": "done"})
            self.assertEqual(self.rows(d, "runs.jsonl"), [])
            (skipped,) = self.rows(d, "skipped.jsonl")
            self.assertEqual(skipped["reason"], "awaiting_handback")
            self.run_hook(d, base + [enforce, new_handback], {"last_assistant_message": "done"})
            (row,) = self.rows(d, "runs.jsonl")
            self.assertEqual(row["report_source"], "handback")
            self.assertEqual(row["report_chars"], len(new))

    def test_api_request_blob_does_not_resurrect_handback(self) -> None:
        old = self.handback(report("Result", "BLOCKED", ["Changes", "Verification"]))
        meta = {"type": "user", "isMeta": True,
                "message": {"role": "user", "content": "continue"}}
        blob = {"type": "api-request-blob", "message": old["message"]}
        text_end = {"type": "assistant",
                    "message": {"id": "m3", "role": "assistant", "model": "claude-sonnet-5",
                                "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1},
                                "content": [{"type": "text", "text": "done"}]}}
        records = [self.BRIEF, self.REMINDER, self.BASH, old, meta, blob, text_end]
        with tempfile.TemporaryDirectory() as d:
            self.run_hook(d, records, {"last_assistant_message": "done"})
            self.assertEqual(self.rows(d, "runs.jsonl"), [])
            (skipped,) = self.rows(d, "skipped.jsonl")
            self.assertEqual(skipped["reason"], "awaiting_handback")

    def test_trailing_text_after_handback_keeps_it(self) -> None:
        text = report("Result", "DONE", ["Changes", "Verification"])
        tool_result = {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}
        text_end = {"type": "assistant",
                    "message": {"id": "m3", "role": "assistant", "model": "claude-sonnet-5",
                                "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1},
                                "content": [{"type": "text", "text": "done"}]}}
        records = [self.BRIEF, self.REMINDER, self.BASH, self.handback(text), tool_result, text_end]
        with tempfile.TemporaryDirectory() as d:
            self.run_hook(d, records, {"last_assistant_message": "done"})
            (row,) = self.rows(d, "runs.jsonl")
            self.assertEqual(row["report_source"], "handback")
            self.assertEqual(row["report_chars"], len(text))
            self.assertEqual(self.rows(d, "skipped.jsonl"), [])

    def test_non_list_meta_content_does_not_lose_row(self) -> None:
        meta = {"type": "user", "isMeta": True, "message": {"role": "user", "content": 5}}
        with tempfile.TemporaryDirectory() as d:
            self.run_hook(d, [self.BRIEF, meta, self.BASH], {"last_assistant_message": "x"})
            self.assertEqual(len(self.rows(d, "runs.jsonl")), 1)

    def test_old_cli_uses_last_assistant_message(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.run_hook(d, [self.BRIEF, self.BASH],
                          {"last_assistant_message": report("Result", "DONE", ["Changes", "Verification"])})
            (row,) = self.rows(d, "runs.jsonl")
            self.assertEqual(row["report_source"], "last_assistant_message")
            self.assertEqual(row["contract_status"], "valid")

    def test_no_report_anywhere(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.run_hook(d, [self.BRIEF, self.BASH])
            (row,) = self.rows(d, "runs.jsonl")
            self.assertEqual(row["report_source"], "none")
            self.assertEqual(row["report_metadata_status"], "unavailable")


class RelaxedStatusLineTests(unittest.TestCase):
    def validate(self, status_line: str) -> tuple[str | None, str, list[str]]:
        text = report("Result", "DONE", ["Changes", "Verification"]).replace("DONE — summary", status_line)
        verdict, _, contract, reasons = HOOK.validate_report("code-writer-t2", True, text)
        return verdict, contract, reasons

    def test_nonstandard_status_forms_keep_verdict_and_flag_contract(self) -> None:
        for line in ("`DONE`: summary", "DONE: summary", "`DONE`. summary", "**DONE** — summary", "`DONE`, summary"):
            with self.subTest(line=line):
                verdict, contract, reasons = self.validate(line)
                self.assertEqual(verdict, "DONE")
                self.assertEqual(contract, "invalid")
                self.assertEqual(reasons, ["nonstandard_status_line"])

    def test_standard_status_stays_valid(self) -> None:
        self.assertEqual(self.validate("`DONE` — summary"), ("DONE", "valid", []))

    def test_lookalike_statuses_yield_no_verdict(self) -> None:
        for line in ("DONEX: summary", "DONE_FOO — summary", "done: summary", "NOT DONE: summary"):
            with self.subTest(line=line):
                verdict, contract, reasons = self.validate(line)
                self.assertIsNone(verdict)
                self.assertIn("missing_or_invalid_status", reasons)
                self.assertNotIn("nonstandard_status_line", reasons)


class BackfillTests(unittest.TestCase):
    TOOL = CLAUDE_DIR / "tools" / "backfill-verdicts.py"
    REPORT = "### Result\n`DONE`: ok\n### Changes\nc\n### Verification\nv"

    def row(self, agent_id: str) -> dict:
        return {"agent_id": agent_id, "agent_type": "code-writer-t2", "verdict": None,
                        "verdict_status": "lost", "report_source": None, "contract_status": "unavailable"}

    def setup_dir(self, d: str, rows: list[dict], transcript_ids: list[str]) -> tuple[Path, Path]:
        root = Path(d)
        log = root / "runs.jsonl"
        log.write_text("".join(json.dumps(r) + "\n" for r in rows))
        record = {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "SubagentHandback", "input": {"message": self.REPORT}}]}}
        for agent_id in transcript_ids:
            sub = root / "projects" / "p" / "s" / "subagents"
            sub.mkdir(parents=True, exist_ok=True)
            (sub / f"agent-{agent_id}.jsonl").write_text(json.dumps(record) + "\n")
        return log, root / "out.jsonl"

    def run_tool(self, log: Path, out: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-B", str(self.TOOL), "--log", str(log), "--out", str(out),
              "--projects-dir", str(log.parent / "projects")],
            capture_output=True, text=True)

    def test_backfills_from_handback_and_leaves_rows_without_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            log, out = self.setup_dir(d, [self.row("a1"), self.row("a2")], ["a1"])
            result = self.run_tool(log, out)
            self.assertEqual(result.returncode, 0)
            first, second = (json.loads(line) for line in out.read_text().splitlines())
            self.assertEqual(first["verdict"], "DONE")
            self.assertEqual(first["verdict_status"], "captured")
            self.assertEqual(first["report_source"], "handback")
            self.assertEqual(first["contract_reasons"], ["nonstandard_status_line"])
            self.assertRegex(first["verdict_backfilled"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertNotIn("backfilled", first)
            self.assertEqual(first["report_chars"], len(self.REPORT))
            self.assertIsNone(second["verdict"])
            self.assertNotIn("verdict_backfilled", second)
            self.assertIn("no_transcript: 1", result.stdout)

    def test_shared_agent_id_backfills_only_last_row(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            log, out = self.setup_dir(d, [self.row("a1"), self.row("a1")], ["a1"])
            result = self.run_tool(log, out)
            first, second = (json.loads(line) for line in out.read_text().splitlines())
            self.assertIsNone(first["verdict"])
            self.assertEqual(second["verdict"], "DONE")
            self.assertIn("skipped_ambiguous: 1", result.stdout)

    def test_keeps_existing_backfilled_marker_and_fixes_stale_report_fields(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            row = self.row("a1") | {"backfilled": "2026-11-04", "report_source": "none", "report_chars": 0}
            log, out = self.setup_dir(d, [row], ["a1"])
            self.run_tool(log, out)
            (got,) = (json.loads(line) for line in out.read_text().splitlines())
            self.assertEqual(got["backfilled"], "2026-11-04")
            self.assertEqual(got["report_source"], "handback")
            self.assertEqual(got["report_chars"], len(self.REPORT))

    def test_continued_agent_does_not_inherit_earlier_run_text(self) -> None:
        def rec(role: str, content: object, ts: str) -> str:
            return json.dumps({"type": role, "timestamp": ts, "message": {"role": role, "content": content}}) + "\n"
        with tempfile.TemporaryDirectory() as d:
            first = self.row("x1") | {"timestamp": "2026-11-01T10:00:00+00:00"}
            second = self.row("x1") | {"timestamp": "2026-11-01T11:00:00+00:00", "stop_reason": "max_tokens"}
            log, out = self.setup_dir(d, [first, second], [])
            sub = Path(d) / "projects" / "p" / "s" / "subagents"
            sub.mkdir(parents=True)
            (sub / "agent-x1.jsonl").write_text(
                rec("user", "brief", "2026-11-01T09:00:00Z")
                + rec("assistant", [{"type": "text", "text": self.REPORT}], "2026-11-01T10:00:00Z")
                + rec("user", "continue", "2026-11-01T10:30:00Z")
                + rec("assistant", [{"type": "tool_use", "name": "Bash", "input": {}}], "2026-11-01T10:31:00Z"))
            result = self.run_tool(log, out)
            rows = [json.loads(line) for line in out.read_text().splitlines()]
            self.assertIsNone(rows[0]["verdict"])
            self.assertIsNone(rows[1]["verdict"])
            self.assertIn("no_report: 1", result.stdout)

    def test_null_agent_id_row_unchanged_and_counted_separately(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            row = self.row("a1") | {"agent_id": None}
            log, out = self.setup_dir(d, [row], [])
            result = self.run_tool(log, out)
            self.assertEqual(json.loads(out.read_text()), row)
            self.assertIn("skipped_null_id: 1", result.stdout)
            self.assertIn("skipped_ambiguous: 0", result.stdout)

    def test_refuses_out_equal_to_log(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            log, _ = self.setup_dir(d, [self.row("a1")], ["a1"])
            before = log.read_text()
            result = self.run_tool(log, log)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(log.read_text(), before)


class TierOfTests(unittest.TestCase):
    def test_tier_of_prefers_explicit_tier_then_model_family(self) -> None:
        cases = (
            ({"tier": "T3", "model": "claude-haiku"}, "T3"),
            ({"model": "claude-haiku"}, "T1"),
            ({"model": "claude-sonnet", "effort": "max"}, "T3"),
            ({"model": "claude-sonnet", "effort": "medium"}, "T2"),
            ({"model": "claude-opus"}, "?(legacy-opus)"),
            ({"model": "claude-fable"}, "?(fable)"),
            ({"model": "unknown-model"}, "?"),
        )
        for rec, expected in cases:
            with self.subTest(rec=rec):
                self.assertEqual(FIRST_PASS.tier_of(rec), expected)


if __name__ == "__main__":
    unittest.main()
