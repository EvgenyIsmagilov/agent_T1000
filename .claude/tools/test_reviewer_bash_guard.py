#!/usr/bin/env python3
"""Regression tests for reviewer-bash-guard.py — the PreToolUse gate that
restricts the read-only `reviewer` subagent's Bash calls to commands that
cannot modify the working tree or files.

Loads the hook module directly (no subprocess) for the allow/deny table so
LOG_PATH can be pointed at a temp file — tests must never write to
~/.claude/logs. A separate class runs the real script via subprocess
against a LOG_PATH-patched copy, to check the on-the-wire JSON shape and
the fail-closed path for malformed hook input.
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
from unittest.mock import patch

CLAUDE_DIR = Path.home() / ".claude"
HOOK_PATH = CLAUDE_DIR / "hooks" / "reviewer-bash-guard.py"


def load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("reviewer_bash_guard", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class EvaluateTableTests(unittest.TestCase):
    """Allow/deny table over the public surface: `evaluate(command) -> (bool, str)`."""

    # (команда, переменные окружения для этого случая)
    ALLOW = [
        ("git diff main...HEAD && rg foo", {}),
        ('rg "a|b" src', {}),
        ("echo x > $TMPDIR/review-a.md", {"TMPDIR": "/tmp/claude-501"}),
        ("echo x >> $TMPDIR/review-a.md", {"TMPDIR": "/tmp/claude-501"}),
        ("echo x > ${TMPDIR}/review-a.md", {"TMPDIR": "/tmp/claude-501"}),
        ("python3 -c 'print(1)'", {}),
        # настоящий перевод строки внутри '...'/"..." — часть аргумента -c, а не разделитель сегментов
        ("python3 -c 'import sys\nprint(1)'", {}),
        ('python3 -c "import sys\nprint(1)"', {}),
        # "<<" внутри кавычек — обычный текст аргумента, а не heredoc-оператор
        ("python3 -c 'print(1 << 4)'", {}),
        ("rg '<<' src", {}),
        ("ls 2>/dev/null", {}),
        ("pytest tests/test_x.py::test_a", {}),
        ("pytest -k foo", {}),
        ("uv run python -c 'print(1)'", {}),
        ("uv run pytest -k foo", {}),
        ("git -C /tmp diff", {}),
        ("echo x &>/dev/null", {}),
        ("echo x 2>&1", {}),
        ("grep 'x;y' file", {}),
        ("ls\npwd", {}),
        # путь уже развёрнут (не содержит буквального $TMPDIR) и лежит в поддереве TMPDIR
        (
            "echo x > /private/tmp/claude-501/x/y/scratchpad/review-a.md",
            {"TMPDIR": "/tmp/claude-501"},
        ),
    ]

    DENY = [
        ("git stash", {}),
        ("git checkout -- .", {}),
        ("echo x > $TMPDIR/../evil.md", {"TMPDIR": "/tmp/claude-501"}),
        ("echo x>out.txt", {}),
        # sed/awk/sort/uniq/find/file больше не в списке разрешённых — отказ по умолчанию
        ("sed -n 1,5p f", {}),
        ("awk '{print}' f", {}),
        ("sort f", {}),
        ("uniq f", {}),
        ("find . -name x", {}),
        ("file x", {}),
        ("git diff --output=x", {}),
        ("git diff --output x", {}),
        ("cat $(echo f)", {}),
        ("bash -c 'ls'", {}),
        ("sh -c 'ls'", {}),
        ("FOO=1 ls", {}),
        ("pytest", {}),
        ("uv run pytest", {}),
        ("git grep -O foo", {}),
        ("git grep --open-files-in-pager foo", {}),
        ("rg --pre=cat foo", {}),
        ("ls; rm foo", {}),
        ("ls\nrm -rf /", {}),  # перевод строки — разделитель, а не пробел внутри аргумента
        ("echo `id`", {}),
        ("cat <(echo f)", {}),
        (". ./script.sh", {}),
        ("git config user.name x", {}),
        # heredoc/here-string запрещены всегда, даже для «python3 -» — см. докстринг модуля
        ("python3 - <<'EOF'\nprint(1)\nEOF", {}),
        # HIGH-находка, которую заменяет отказ: раньше конец тела ловился по токену,
        # а не по строке, и следующая команда после тела проходила незамеченной
        (
            "python3 - <<'EOF'\n# don't\nEOF\ntouch marker.txt\npython3 - <<'EOF'\n# it's\nEOF",
            {},
        ),
        ("cat <<EOF\nx\nEOF", {}),
        ("python3 - <<-'EOF'\n\tprint(1)\n\tEOF", {}),
        ("cat <<< x", {}),  # here-string — тоже из семейства heredoc
        ("python3 -", {}),  # чтение скрипта со stdin убрано вместе с heredoc-формой
        # кавыченный decoy «<<» не должен помешать find_raw_heredoc_operator найти
        # настоящий heredoc-оператор дальше в той же строке
        (
            "echo \"<<'X'\" && python3 - <<EOF\nprint(1)\nEOF",
            {},
        ),
        # MEDIUM: следующий символ после "<<"/"<<<" оказывается последним в строке —
        # раньше это путало "конец строки" с "опережающий символ прочитан", и токен проходил как аргумент
        ("cat <<x", {}),
        ("cat <<<x", {}),
        ("cat x<<<y", {}),
        ("cat <<-", {}),
        ("cat << ", {}),
        ('echo "unterminated', {}),  # незакрытая кавычка — не разобрать
        ("echo x $(rm -rf /)", {}),
        (
            "echo x > $TMPDIR/sub/../../evil/review-a.md",
            {"TMPDIR": "/tmp/claude-501"},
        ),
        ("echo x > /etc/review-a.md", {"TMPDIR": "/tmp/claude-501"}),
        ("echo x > $TMPDIR/notes.md", {"TMPDIR": "/tmp/claude-501"}),  # не review-*.md
        # фигурные скобки — brace expansion шелла может увести путь за пределы TMPDIR
        (
            "echo x > $TMPDIR/{..,x}/review-a.md",
            {"TMPDIR": "/tmp/claude-501"},
        ),
        (
            "echo x > ${TMPDIR}/review-{a,b}.md",
            {"TMPDIR": "/tmp/claude-501"},
        ),
        # LOW: "$TMPDIRz" — другая, необъявленная переменная, а не "$TMPDIR"+"z";
        # наивная текстовая замена раньше принимала её за TMPDIR и путь проходил
        (
            "echo x > $TMPDIRz/../claude-501/review-a.md",
            {"TMPDIR": "/tmp/claude-501"},
        ),
        # глоб-символы — shell-глоббинг может подставить не тот файл
        (
            "echo x > $TMPDIR/review-[ab].md",
            {"TMPDIR": "/tmp/claude-501"},
        ),
        ("echo x > review-a.md", {"TMPDIR": ""}),  # TMPDIR не задан
        # подстановка команд, спрятанная в двойных кавычках или приклеенная к слову
        ('cat "$(git stash)"', {}),
        ("echo x`touch evil`", {}),
        ("cat foo`x`", {}),
        # "${" запрещён везде, кроме буквального "${TMPDIR}" в начале цели редиректа —
        # zsh-флаги вроде "${(e)...}" исполняют содержимое как команду
        ("echo ${(e)x}", {}),
        (
            "echo x > ${TMPDIR}${evil}/review-a.md",
            {"TMPDIR": "/tmp/claude-501"},
        ),
    ]

    @classmethod
    def setUpClass(cls):
        cls.hook = load_hook()

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            prefix="reviewer-bash-guard-", suffix=".jsonl", delete=False
        )
        self.hook.LOG_PATH = Path(self.tmp.name)
        self.addCleanup(lambda: Path(self.tmp.name).unlink(missing_ok=True))

    def test_allowed_commands(self):
        for command, env in self.ALLOW:
            with self.subTest(command=command):
                with patch.dict("os.environ", env):
                    ok, reason = self.hook.evaluate(command)
                self.assertTrue(ok, f"expected allow, got deny: {reason}")

    def test_denied_commands(self):
        for command, env in self.DENY:
            with self.subTest(command=command):
                with patch.dict("os.environ", env):
                    ok, reason = self.hook.evaluate(command)
                self.assertFalse(ok, "expected deny, got allow")
                self.assertTrue(reason)


class MainFunctionTests(unittest.TestCase):
    """main() вызывается напрямую (без subprocess) — тот же код, что и в реальном хуке."""

    def setUp(self):
        self.hook = load_hook()
        self.tmp = tempfile.NamedTemporaryFile(
            prefix="reviewer-bash-guard-", suffix=".jsonl", delete=False
        )
        self.hook.LOG_PATH = Path(self.tmp.name)
        self.addCleanup(lambda: Path(self.tmp.name).unlink(missing_ok=True))

    def run_main(self, payload: dict) -> tuple[str, str]:
        old_stdin, old_stdout = sys.stdin, sys.stdout
        try:
            sys.stdin = io.StringIO(json.dumps(payload))
            sys.stdout = io.StringIO()
            with self.assertRaises(SystemExit):
                self.hook.main()
            output = sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = old_stdin, old_stdout
        decision = json.loads(output)["hookSpecificOutput"]
        return decision["permissionDecision"], decision.get("permissionDecisionReason", "")

    def test_missing_command_denied(self):
        decision, reason = self.run_main({"tool_input": {}})
        self.assertEqual(decision, "deny")
        self.assertTrue(reason)

    def test_empty_command_denied(self):
        decision, _ = self.run_main({"tool_input": {"command": "   "}})
        self.assertEqual(decision, "deny")

    def test_valid_command_allowed(self):
        decision, _ = self.run_main({"tool_input": {"command": "pwd"}})
        self.assertEqual(decision, "allow")


class EndToEndSubprocessTests(unittest.TestCase):
    """Настоящий запуск скрипта отдельным процессом со stdin JSON — проверяет
    форму ответа (hookSpecificOutput.permissionDecision) и fail-closed путь
    при некорректном JSON на входе хука."""

    def write_patched_copy(self, tmp_dir: Path, log_path: Path) -> Path:
        source = HOOK_PATH.read_text()
        patched = source.replace(
            'LOG_PATH = Path.home() / ".claude" / "logs" / "reviewer-bash-guard.jsonl"',
            f"LOG_PATH = Path({str(log_path)!r})",
        )
        self.assertNotEqual(patched, source, "LOG_PATH assignment not found to patch")
        copy_path = tmp_dir / "reviewer-bash-guard.py"
        copy_path.write_text(patched)
        return copy_path

    def run_subprocess(self, stdin_text: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            copy_path = self.write_patched_copy(tmp_path, tmp_path / "log.jsonl")
            return subprocess.run(
                [sys.executable, str(copy_path)],
                input=stdin_text,
                capture_output=True,
                text=True,
            )

    def test_allow_e2e(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        result = self.run_subprocess(json.dumps(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "allow")

    def test_deny_command_e2e(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "git stash"}}
        result = self.run_subprocess(json.dumps(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertTrue(decision.get("permissionDecisionReason"))

    def test_deny_malformed_stdin_json_e2e(self):
        result = self.run_subprocess("{not valid json")
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertTrue(decision.get("permissionDecisionReason"))


if __name__ == "__main__":
    unittest.main()
