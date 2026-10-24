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
FIXTURES_DIR = CLAUDE_DIR / "tools" / "fixtures" / "reports"


def load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("log_subagent", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


HOOK = load_hook()


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


if __name__ == "__main__":
    unittest.main()
