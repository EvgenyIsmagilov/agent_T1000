"""Tests for agent-model-gate.py: run the hook as a subprocess, HOME in a temp dir."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "agent-model-gate.py"
SS = "mcp__ccd_session__start_session"


def human(text):
    return {"type": "user", "origin": {"kind": "human"}, "message": {"content": text}}


def assistant(model):
    return {"type": "assistant", "message": {"model": model, "content": []}}


class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def transcript(self, *records):
        path = self.dir / "t.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in records))
        return str(path)

    def run_proc(self, tool, tool_input, transcript=None):
        payload = {"tool_name": tool, "tool_input": tool_input, "transcript_path": transcript}
        return subprocess.run(
            [sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
            capture_output=True, env={"HOME": str(self.dir), "PATH": "/usr/bin:/bin"},
        )

    def run_hook(self, tool, tool_input, transcript=None):
        out = self.run_proc(tool, tool_input, transcript).stdout
        return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out.strip() else None

    def ss(self, tool_input, *records):
        return self.run_hook(SS, tool_input, self.transcript(*records) if records else None)

    # start_session
    def test_offer_title_only_allowed(self):
        self.assertIsNone(self.ss({"initiation": "offer", "title": "x"}, assistant("claude-opus-5-5")))

    def log_text(self):
        return (self.dir / ".claude" / "logs" / "agent-model-gate.jsonl").read_text()

    def test_odd_promptless_shapes_allowed_and_logged(self):
        shapes = ({"proposal_id": "p", "model": "opus"}, {"context": "fork", "title": "x"},
                  {"prompt": "", "model": "opus"})
        for shape in shapes:
            self.assertIsNone(self.ss(shape, assistant("claude-opus-5-5")))
        self.assertEqual(self.log_text().count("allow-unchecked-shape"), 3)

    def test_offer_and_bare_proposal_id_not_logged(self):
        self.ss({"initiation": "offer", "title": "x"}, assistant("claude-opus-5-5"))
        self.ss({"proposal_id": "p", "wake_when_done": True}, assistant("claude-opus-5-5"))
        self.assertFalse((self.dir / ".claude").exists())

    def test_fresh_opus_missing_transcript(self):
        for path in (None, str(self.dir / "missing"), str(self.dir)):
            self.assertIsNone(self.run_hook(SS, {"prompt": "go", "model": "opus"}, path))
        self.assertEqual(self.log_text().count("allow-no-transcript"), 3)

    @unittest.skipIf(os.geteuid() == 0, "root reads a file with mode 0")
    def test_fresh_opus_unreadable_transcript_allowed(self):
        path = self.transcript(human("сделай"))
        os.chmod(path, 0)
        self.addCleanup(os.chmod, path, 0o644)
        self.assertIsNone(self.run_hook(SS, {"prompt": "go", "model": "opus"}, path))
        self.assertEqual(self.log_text().count("allow-no-transcript"), 1)
        self.assertIn("PermissionError, errno=13", self.log_text())
        self.assertNotIn("PermissionError(", self.log_text())

    def test_bare_proposal_id_allowed(self):
        self.assertIsNone(self.ss({"proposal_id": "p1"}, assistant("claude-opus-5-5")))

    def test_proposal_id_prompt_no_model_opus_parent_denied(self):
        self.assertEqual(self.ss({"proposal_id": "p1", "prompt": "go"}, assistant("claude-opus-5-5")), "deny")

    def test_fresh_no_model_premium_parent_denied(self):
        self.assertEqual(self.ss({"prompt": "go"}, assistant("claude-opus-5-5")), "deny")

    def test_fresh_no_model_unknown_parent_denied(self):
        self.assertEqual(self.ss({"prompt": "go"}), "deny")

    def test_fresh_no_model_sonnet_parent_allowed(self):
        self.assertIsNone(self.ss({"prompt": "go"}, assistant("claude-sonnet-5-5")))

    def test_fresh_sonnet_allowed(self):
        self.assertIsNone(self.ss({"prompt": "go", "model": "sonnet"}, assistant("claude-opus-5-5")))

    def test_fresh_opus_no_approval_denied(self):
        self.assertEqual(self.ss({"prompt": "go", "model": "opus"}, human("сделай"), assistant("claude-sonnet-5-5")), "deny")

    def test_fresh_opus_approved_allowed(self):
        self.assertIsNone(self.ss({"prompt": "go", "model": "opus"}, human("да, opus"), assistant("claude-sonnet-5-5")))

    def test_fresh_opus_approval_wrong_family_denied(self):
        self.assertEqual(self.ss({"prompt": "go", "model": "opus"}, human("да, fable")), "deny")

    def test_fork_opus_parent_no_approval_denied(self):
        self.assertEqual(self.ss({"prompt": "go", "context": "fork"}, human("ok"), assistant("claude-opus-5-5")), "deny")

    def test_fork_opus_parent_approved_allowed(self):
        self.assertIsNone(self.ss({"prompt": "go", "context": "fork"}, human("да, опус"), assistant("claude-opus-5-5")))

    def test_trailing_synthetic_record_ignored(self):
        self.assertEqual(self.ss({"prompt": "go"}, assistant("claude-opus-5-5"), assistant("<synthetic>")), "deny")

    def test_sidechain_record_ignored(self):
        side = {**assistant("claude-sonnet-5-5"), "isSidechain": True}
        self.assertEqual(self.ss({"prompt": "go", "context": "fork"}, assistant("claude-opus-5-5"), side), "deny")

    def test_fork_sonnet_parent_allowed(self):
        self.assertIsNone(self.ss({"prompt": "go", "context": "fork"}, assistant("claude-sonnet-5-5")))

    def test_fork_unreadable_transcript_allowed(self):
        self.assertIsNone(self.run_hook(SS, {"prompt": "go", "context": "fork"}, str(self.dir / "missing")))

    def test_cloud_and_preset_allowed_and_logged(self):
        for extra in ({"target": "cloud"}, {"preset": "p"}):
            self.assertIsNone(self.ss({"prompt": "go", **extra}, assistant("claude-opus-5-5")))
        log = (self.dir / ".claude" / "logs" / "agent-model-gate.jsonl").read_text()
        self.assertEqual(log.count("allow-unchecked-target"), 2)
        self.assertIn('"start_session"', log)

    # Agent regression
    def test_agent_general_purpose_no_model_denied(self):
        self.assertEqual(self.run_hook("Agent", {"subagent_type": "general-purpose"}), "deny")

    def test_agent_general_purpose_sonnet_allowed(self):
        self.assertIsNone(self.run_hook("Agent", {"subagent_type": "general-purpose", "model": "sonnet"}))

    # repr(e) quotes the raw bytes of a decode error, so the log keeps only the type.
    def test_invalid_utf8_transcript_logs_type_only(self):
        path = self.dir / "bad.jsonl"
        path.write_bytes(b"\xff\xfe SECRET-MARKER-xyz \xff")
        cases = (
            ("Agent", {"subagent_type": "general-purpose", "model": "opus", "prompt": "x"}),
            (SS, {"prompt": "go", "model": "opus"}),
            (SS, {"prompt": "go"}),
        )
        for tool, tool_input in cases:
            with self.subTest(tool=tool, tool_input=tool_input):
                proc = self.run_proc(tool, tool_input, str(path))
                self.assertEqual(proc.returncode, 0)
                self.assertEqual(proc.stdout, "")
        self.assertEqual(self.log_text().count('"allow-on-error"'), len(cases))
        self.assertNotIn("SECRET-MARKER-xyz", self.log_text())

    @unittest.skipIf(os.geteuid() == 0, "root reads a file with mode 0")
    def test_agent_unreadable_transcript_logs_errno(self):
        path = self.transcript(human("сделай"))
        os.chmod(path, 0)
        self.addCleanup(os.chmod, path, 0o644)
        self.assertIsNone(self.run_hook("Agent", {"subagent_type": "general-purpose", "model": "opus"}, path))
        self.assertIn("PermissionError, errno=13", self.log_text())


if __name__ == "__main__":
    unittest.main()
