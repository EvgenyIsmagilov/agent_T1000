#!/usr/bin/env python3
"""Tests for the orchestration-exit-gate.py PreToolUse hook.

Run main() in-process with fake stdin. Both log paths point at temp files,
so nothing is written under ~/.claude/logs.
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HOOK_PATH = Path(__file__).resolve().parent.parent / "hooks" / "orchestration-exit-gate.py"
SID = "sess-1"


def load_hook():
    spec = importlib.util.spec_from_file_location("orchestration_exit_gate", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(agent, slice_id, ts, session=SID, schema=5):
    return {"timestamp": ts, "session_id": session, "schema": schema,
            "agent_type": agent, "slice_id": slice_id}


class ExitGateTests(unittest.TestCase):
    def setUp(self):
        self.hook = load_hook()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.runs = Path(self.dir.name) / "runs.jsonl"
        self.own = Path(self.dir.name) / "own.jsonl"
        self.env = {"CLAUDE_SUBAGENT_LOG": str(self.runs), "CLAUDE_EXIT_GATE_LOG": str(self.own)}

    def write_rows(self, *rows):
        self.runs.write_text("".join(json.dumps(r) + "\n" for r in rows))

    def run_raw(self, stdin_text, env=None):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {**self.env, **(env or {})}), \
                mock.patch.object(sys, "stdin", io.StringIO(stdin_text)), \
                mock.patch.object(sys, "stdout", out):
            try:
                self.hook.main()
                code = 0
            except SystemExit as e:
                code = e.code or 0
        self.assertEqual(code, 0)
        return out.getvalue()

    def call(self, tool_name, tool_input, env=None, session=SID):
        payload = {"tool_name": tool_name, "tool_input": tool_input}
        if session:
            payload["session_id"] = session
        out = self.run_raw(json.dumps(payload), env)
        if not out:
            return None
        decision = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        return decision["permissionDecisionReason"]

    def bash(self, command, **kw):
        return self.call("Bash", {"command": command}, **kw)

    def agent(self, slice_id, agent="code-writer-t2"):
        return self.call("Agent", {"subagent_type": agent, "prompt": f"slice_id: {slice_id}\ntier: T2"})

    def wrap_up(self, recap):
        return self.call("mcp__ccd_turn__wrap_up", {"recap": recap})

    # 1
    def test_no_runs_log_passes(self):
        self.assertIsNone(self.bash("git commit -m x"))

    # 2
    def test_validated_and_reviewed_commit_passes(self):
        self.write_rows(row("code-writer-t2", "a", "2026-01-01T00:00:01"),
                        row("test-runner", "a", "2026-01-01T00:00:02"),
                        row("reviewer", None, "2026-01-01T00:00:03"))
        self.assertIsNone(self.bash("git commit -m x"))

    # 3
    def test_slice_without_test_runner(self):
        self.write_rows(row("code-writer-t2", "a", "2026-01-01T00:00:01"))
        for command in ("git commit -m x", "git push", "gh pr create --fill", "gh pr merge 5"):
            reason = self.bash(command)
            self.assertIn("test-runner с slice_id: a", reason, command)
            self.assertIn("CLAUDE_EXIT_GATE=0", reason)
        self.assertIn("slice_id: a", self.agent("b"))
        self.assertIsNone(self.agent("a"))
        self.assertIn("test-runner", self.wrap_up("done"))
        self.assertIsNone(self.wrap_up("PARTIAL: tests missing"))
        self.assertIsNone(self.wrap_up("BLOCKED on env"))
        self.assertIn("test-runner", self.wrap_up("partially done"))  # not the whole word
        self.assertIn("test-runner", self.wrap_up("no BLOCKED items, all done"))

    def test_agent_without_parsable_slice_is_other_slice(self):
        self.write_rows(row("code-writer-t2", "a", "2026-01-01T00:00:01"))
        self.assertIsNotNone(self.call("Agent", {"subagent_type": "code-writer-t1", "prompt": "no id"}))

    # 4
    def test_stale_test_runner_leaves_slice_open(self):
        self.write_rows(row("code-writer-t2", "a", "2026-01-01T00:00:01"),
                        row("test-runner", "a", "2026-01-01T00:00:02"),
                        row("code-writer-t2", "a", "2026-01-01T00:00:03"))
        self.assertIn("test-runner", self.bash("git commit -m x"))

    # 5
    def test_validated_without_reviewer(self):
        self.write_rows(row("code-writer-t2", "a", "2026-01-01T00:00:01"),
                        row("test-runner", "a", "2026-01-01T00:00:02"))
        reason = self.bash("git commit -m x")
        self.assertIn("reviewer", reason)
        self.assertNotIn("slice_id: a", reason)
        self.assertIsNone(self.agent("b"))
        self.assertIsNone(self.wrap_up("done"))

    # 6
    def test_command_matching(self):
        self.write_rows(row("code-writer-t2", "a", "2026-01-01T00:00:01"))
        for command in ("git commit-tree abc", "gh pr view 3", "gh pr checks", "gh pr edit 3",
                        "git log --oneline", "git push-something", "git status"):
            self.assertIsNone(self.bash(command), command)
        start = time.monotonic()
        self.assertIsNone(self.bash("git " + "--no-pager " * 40 + "log"))
        self.assertLess(time.monotonic() - start, 0.1)
        start = time.monotonic()
        self.assertIsNone(self.bash("git " + '-C "p" ' * 40 + "log"))
        self.assertLess(time.monotonic() - start, 0.1)
        for command in ("git -C /x commit -m m", "cd /x && git push origin HEAD",
                        "git add . ; git commit -m m", "git --no-pager push",
                        'git -C "/Users/e/My Projects/x" push', "gh --repo=o/r pr create"):
            self.assertIsNotNone(self.bash(command), command)

    # 7
    def test_other_session_and_legacy_schema_ignored(self):
        self.write_rows(row("code-writer-t2", "a", "2026-01-01T00:00:01", session="other"),
                        row("code-writer-t2", "b", "2026-01-01T00:00:01", schema=3))
        self.assertIsNone(self.bash("git commit -m x"))

    # 8
    def test_kill_switch(self):
        self.write_rows(row("code-writer-t2", "a", "2026-01-01T00:00:01"))
        self.assertIsNone(self.bash("git commit -m x", env={"CLAUDE_EXIT_GATE": "0"}))

    def test_missing_session_id_passes(self):
        self.write_rows(row("code-writer-t2", "a", "2026-01-01T00:00:01"))
        self.assertIsNone(self.bash("git commit -m x", session=None))

    # 9
    def test_other_tools_pass_silently(self):
        self.write_rows(row("code-writer-t2", "a", "2026-01-01T00:00:01"))
        self.assertEqual(self.run_raw(json.dumps(
            {"tool_name": "Read", "tool_input": {}, "session_id": SID})), "")
        self.assertIsNone(self.call("Agent", {"subagent_type": "reviewer", "prompt": "slice_id: z"}))

    # 10
    def test_malformed_stdin_fails_open(self):
        self.assertEqual(self.run_raw("{not json"), "")
        lines = self.own.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["decision"], "allow-on-error")

    def test_deny_is_logged(self):
        self.write_rows(row("code-writer-t2", "a", "2026-01-01T00:00:01"))
        self.bash("git push")
        entry = json.loads(self.own.read_text().splitlines()[0])
        self.assertEqual((entry["decision"], entry["tool_name"]), ("deny", "Bash"))

    # 11
    def test_legacy_code_writer_is_implementer(self):
        self.write_rows(row("code-writer", "a", "2026-01-01T00:00:01"))
        self.assertIsNotNone(self.bash("git commit -m x"))

    # 12
    def test_malformed_log_line_skipped(self):
        self.runs.write_text("garbage " + SID + "\n" + json.dumps(
            row("code-writer-t2", "a", "2026-01-01T00:00:01")) + "\n")
        self.assertIsNotNone(self.bash("git commit -m x"))


if __name__ == "__main__":
    unittest.main()
