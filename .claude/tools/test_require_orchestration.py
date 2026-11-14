#!/usr/bin/env python3
"""Regression tests for the require-orchestration.py PreToolUse gate.

Loads the hook module directly (no subprocess) so LOG_PATH can be pointed at
a temp file — the hook has no env knob for its log path, and tests must
never write to ~/.claude/logs.
"""
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

CLAUDE_DIR = Path.home() / ".claude"
HOOK_PATH = CLAUDE_DIR / "hooks" / "require-orchestration.py"


def load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("require_orchestration", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


FULL_CHECKLIST = "invariant_paths: a\ndata_sample: b\nspec_check: c\ndivisible: no \u2014 d\n"


class SliceGateTests(unittest.TestCase):
    def setUp(self):
        self.hook = load_hook()
        self.tmp = tempfile.NamedTemporaryFile(prefix="orchestration-gate-", suffix=".jsonl", delete=False)
        self.hook.LOG_PATH = Path(self.tmp.name)
        self.addCleanup(lambda: Path(self.tmp.name).unlink(missing_ok=True))

    def run_hook(self, tool_input, env=None):
        payload = {
            "tool_name": "Agent",
            "tool_input": tool_input,
            "transcript_path": "/nonexistent/no-such-transcript.jsonl",
        }
        old_stdin, old_stdout = sys.stdin, sys.stdout
        old_environ = dict(__import__("os").environ)
        try:
            if env:
                __import__("os").environ.update(env)
            sys.stdin = io.StringIO(json.dumps(payload))
            sys.stdout = io.StringIO()
            with self.assertRaises(SystemExit):
                self.hook.main()
            output = sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = old_stdin, old_stdout
            __import__("os").environ.clear()
            __import__("os").environ.update(old_environ)
        decision = json.loads(output)["hookSpecificOutput"]
        return decision["permissionDecision"], decision.get("permissionDecisionReason", "")

    def test_deny_code_writer_t1_without_slice_id(self):
        decision, reason = self.run_hook({"subagent_type": "code-writer-t1", "prompt": "do stuff"})
        self.assertEqual(decision, "deny")
        self.assertIn("slice_id", reason)

    def test_deny_test_runner_without_slice_id(self):
        decision, _ = self.run_hook({"subagent_type": "test-runner", "prompt": "do stuff"})
        self.assertEqual(decision, "deny")

    def test_allow_with_slice_id_line(self):
        decision, _ = self.run_hook(
            {"subagent_type": "code-writer-t1", "prompt": "slice_id: foo\ndo stuff"}
        )
        self.assertEqual(decision, "allow")

    def test_allow_with_backticked_list_item_slice_id(self):
        decision, _ = self.run_hook(
            {"subagent_type": "code-writer-t2", "prompt": "- `slice_id`: foo\n" + FULL_CHECKLIST}
        )
        self.assertEqual(decision, "allow")

    def test_allow_reviewer_without_slice_id(self):
        decision, _ = self.run_hook({"subagent_type": "reviewer", "prompt": "review this"})
        self.assertEqual(decision, "allow")

    def test_allow_web_researcher_without_slice_id(self):
        decision, _ = self.run_hook({"subagent_type": "web-researcher", "prompt": "look this up"})
        self.assertEqual(decision, "allow")

    def test_allow_explore_without_slice_id(self):
        decision, _ = self.run_hook({"subagent_type": "Explore", "prompt": "find this"})
        self.assertEqual(decision, "allow")

    def test_bypass_env_var_allows_without_slice_id(self):
        decision, _ = self.run_hook(
            {"subagent_type": "test-runner", "prompt": "do stuff"},
            env={"CLAUDE_SLICE_GATE": "0"},
        )
        self.assertEqual(decision, "allow")

    def test_doctrine_gate_bypass_still_denies_missing_slice_id(self):
        """The two kill switches are independent (CLAUDE_ORCHESTRATION_GATE=0
        must not also silence CLAUDE_SLICE_GATE)."""
        decision, reason = self.run_hook(
            {"subagent_type": "test-runner", "prompt": "do stuff"},
            env={"CLAUDE_ORCHESTRATION_GATE": "0"},
        )
        self.assertEqual(decision, "deny")
        self.assertIn("slice_id", reason)

    def test_checklist_denies_t2_t3_when_lines_missing(self):
        lines = FULL_CHECKLIST.splitlines()
        for agent in ("code-writer-t2", "code-writer-t3"):
            for drop in ([0], [1], [2], [3], [0, 1], [0, 1, 2, 3]):
                kept = [l for i, l in enumerate(lines) if i not in drop]
                prompt = "slice_id: foo\n" + "\n".join(kept)
                decision, reason = self.run_hook({"subagent_type": agent, "prompt": prompt})
                self.assertEqual(decision, "deny", (agent, drop))
                for i in drop:
                    self.assertIn(lines[i].split(":")[0], reason)
                self.assertIn("n/a", reason)
        self.assertIn("deny-checklist", self.tmp_log())

    def tmp_log(self):
        return Path(self.tmp.name).read_text()

    def test_checklist_allows_valid_forms_of_all_fields(self):
        for block in (
            FULL_CHECKLIST,
            "invariant_paths: none \u2014 single file\ndata_sample: n/a\nspec_check: none - no ticket\ndivisible: no - single file\n",
            "- invariant_paths: a\n* `data_sample`: b\n  `spec_check` = c\n  divisible: no, d\n",
            FULL_CHECKLIST.replace("divisible: no \u2014 d", "divisible: no \u2014 one parser"),
            FULL_CHECKLIST.replace("divisible: no \u2014 d", "- `divisible`: yes, three independent modules"),
            FULL_CHECKLIST.replace("divisible: no \u2014 d", "DIVISIBLE = No (shared invariant)"),
            FULL_CHECKLIST.replace("divisible: no \u2014 d", "divisible: yes - two modules"),
            FULL_CHECKLIST.replace("divisible: no \u2014 d", "divisible: no: reason"),
        ):
            decision, _ = self.run_hook(
                {"subagent_type": "code-writer-t3", "prompt": "slice_id: foo\n" + block}
            )
            self.assertEqual(decision, "allow", block)

    def test_divisible_needs_yes_or_no_and_reason(self):
        head = "slice_id: foo\ninvariant_paths: a\ndata_sample: b\nspec_check: c\n"
        for value in ("divisible: yes", "divisible: no", "divisible: maybe \u2014 x",
                      "divisible: yesterday \u2014 x", "divisible:", "divisible: none \u2014 x",
                      "divisible: yes \u2014", "divisible: `yes`",
                      "divisible: yes|no \u2014 \u043f\u0440\u0438\u0447\u0438\u043d\u0430", "divisible: yes/no \u2014 x"):
            decision, reason = self.run_hook({"subagent_type": "code-writer-t2", "prompt": head + value + "\n"})
            self.assertEqual(decision, "deny", value)
            self.assertIn("divisible: no \u2014 \u043e\u0434\u0438\u043d \u043f\u0430\u0440\u0441\u0435\u0440", reason)
        decision, _ = self.run_hook({"subagent_type": "code-writer-t2", "prompt": head})
        self.assertEqual(decision, "deny")

    def test_checklist_denies_empty_value_and_prose_mention(self):
        prompt = "slice_id: foo\ninvariant_paths: a\ndata_sample: b\ndivisible: no - x\nspec_check:   \n"
        decision, reason = self.run_hook({"subagent_type": "code-writer-t2", "prompt": prompt})
        self.assertEqual(decision, "deny")
        self.assertIn("spec_check", reason)
        for junk in ("`", "``", ":", "-", "`-`"):
            prompt = f"slice_id: foo\ninvariant_paths: a\ndata_sample: b\ndivisible: no - x\nspec_check:{junk}\n"
            decision, _ = self.run_hook({"subagent_type": "code-writer-t2", "prompt": prompt})
            self.assertEqual(decision, "deny", junk)
        prose = "slice_id: foo\nFill the spec_check: line, data_sample: and invariant_paths: x here\n"
        decision, _ = self.run_hook({"subagent_type": "code-writer-t2", "prompt": prose})
        self.assertEqual(decision, "deny")

    def test_checklist_does_not_gate_t1_or_test_runner(self):
        for agent in ("code-writer-t1", "test-runner"):
            decision, _ = self.run_hook({"subagent_type": agent, "prompt": "slice_id: foo"})
            self.assertEqual(decision, "allow", agent)

    def test_checklist_kill_switch_is_independent(self):
        prompt = "slice_id: foo"
        decision, _ = self.run_hook(
            {"subagent_type": "code-writer-t2", "prompt": prompt},
            env={"CLAUDE_BRIEF_CHECKLIST_GATE": "0"},
        )
        self.assertEqual(decision, "allow")
        decision, reason = self.run_hook(
            {"subagent_type": "code-writer-t2", "prompt": "no slice"},
            env={"CLAUDE_BRIEF_CHECKLIST_GATE": "0"},
        )
        self.assertEqual(decision, "deny")
        self.assertIn("slice_id", reason)
        for env in ({"CLAUDE_SLICE_GATE": "0"}, {"CLAUDE_ORCHESTRATION_GATE": "0"}):
            decision, reason = self.run_hook({"subagent_type": "code-writer-t2", "prompt": prompt}, env=env)
            self.assertEqual(decision, "deny", env)
            self.assertIn("invariant_paths", reason)

    def test_slice_gate_fires_before_checklist(self):
        decision, reason = self.run_hook({"subagent_type": "code-writer-t2", "prompt": "nothing"})
        self.assertEqual(decision, "deny")
        self.assertIn("slice_id", reason)
        self.assertNotIn("invariant_paths", reason)


class MissingBriefFieldsModuleTests(unittest.TestCase):
    """A copy of the hook without brief_fields.py must fail open, not crash."""

    def test_missing_brief_fields_module_allows_and_logs_allow_on_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            log_path = tmp_path / "orchestration-gate.jsonl"
            source = HOOK_PATH.read_text()
            # Point the copy's log at the temp dir so this test never writes
            # to ~/.claude/logs; brief_fields.py is deliberately not copied.
            patched = source.replace(
                'LOG_PATH = Path.home() / ".claude" / "logs" / "orchestration-gate.jsonl"',
                f"LOG_PATH = Path({str(log_path)!r})",
            )
            self.assertNotEqual(patched, source, "LOG_PATH assignment not found to patch")
            copy_path = tmp_path / "require-orchestration.py"
            copy_path.write_text(patched)

            payload = {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "code-writer-t1", "prompt": "slice_id: foo"},
                "transcript_path": "/nonexistent/no-such-transcript.jsonl",
            }
            result = subprocess.run(
                [sys.executable, str(copy_path)],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            decision = json.loads(result.stdout)["hookSpecificOutput"]
            self.assertEqual(decision["permissionDecision"], "allow")

            entries = [json.loads(line) for line in log_path.read_text().splitlines()]
            self.assertTrue(
                any(entry["decision"] == "allow-on-error" for entry in entries),
                entries,
            )


if __name__ == "__main__":
    unittest.main()
