#!/usr/bin/env python3
"""Regression tests for harness-check.py. Each test runs the script against a temporary ROOT.

The ROOT holds symlinks to the real config entries. Only a directory that a test changes is a copy.
"""
import hashlib
import shutil
import subprocess
import sys
import tempfile
import traceback
import unittest
from collections.abc import Callable
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent / "harness-check.py"
HOME = Path.home()


def mirror(real: Path, link: Path, copies: set[Path]) -> None:
    """Make `link` a directory of symlinks to the entries of `real`. An entry in `copies` is copied."""
    link.mkdir(parents=True)
    for entry in real.iterdir():
        if entry in copies:
            shutil.copytree(entry, link / entry.name)
        else:
            (link / entry.name).symlink_to(entry)


def make_root(root: Path, copies: set[Path]) -> Path:
    for rel in (".claude", ".codex", ".config/opencode"):
        mirror(HOME / rel, root / rel, copies)
    return root


def copy_jsonc(root: Path) -> Path:
    """Replace the opencode.jsonc link under `root` with a copy and return the copy."""
    jsonc = root / ".config/opencode/opencode.jsonc"
    real = jsonc.resolve()
    jsonc.unlink()  # ссылка ведёт в настоящий конфиг, поэтому заменяем её копией в ROOT
    shutil.copyfile(real, jsonc)  # текст не держим в локальной переменной: pytest -l напечатал бы его вместе с ключом
    return jsonc


def text_digest(path: Path) -> str:
    """Return the sha256 of the text of the file, so that two versions compare without being printed."""
    return hashlib.sha256(path.read_text().encode()).hexdigest()


def problem_lines(stdout: str) -> list[str]:
    """Return the problem lines that the script prints as " - <text>"."""
    return [line[3:] for line in stdout.splitlines() if line.startswith(" - ")]


def leaks_file_text(error: Exception, path: Path) -> bool:
    """Return True if the message of `error`, or a local of a frame in its traceback, repeats a run of 20 characters from the file at `path`.

    pytest -l and unittest --locals print these locals.
    The check lives here, not in an assert: a failing assert prints its operands, and the text must not reach the output.
    """
    text = path.read_text()
    shown = [str(error)]
    shown += [repr(value) for frame, _ in traceback.walk_tb(error.__traceback__) for value in frame.f_locals.values()]
    return any(part[i:i + 20] in text for part in shown for i in range(len(part) - 19))


def change_first_line(text: str, heading: str) -> str:
    """Append a word to the first non-blank line under `heading`."""
    lines = text.splitlines(keepends=True)
    start = lines.index(heading + "\n")
    first = next(i for i in range(start + 1, len(lines)) if lines[i].strip())
    lines[first] = lines[first].rstrip("\n") + " (changed)\n"
    return "".join(lines)


def drop_section(text: str, heading: str) -> str:
    lines = text.splitlines(keepends=True)
    start = lines.index(heading + "\n")
    end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("## "))
    return "".join(lines[:start] + lines[end:])


def replace_persona_forms(text: str) -> str:
    """Replace the absolute, resolved, and ~/ persona paths with a path that the check must reject."""
    persona = HOME / ".claude/persona.md"
    foreign = "/elsewhere/.claude/persona.md"  # хвост как у относительного пути ROOT: без .absolute() проверка его примет
    for form in (str(persona), str(persona.resolve()), "~/.claude/persona.md"):
        text = text.replace(form, foreign)
    return text


class HarnessCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def edit(self, path: Path, change: Callable[[str], str]) -> None:
        # Писать можно только в копию под ROOT, а не в файл по ссылке из реального конфига.
        self.assertTrue(path.resolve().is_relative_to(self.root.resolve()), str(path))
        before = text_digest(path)
        try:
            path.write_text(change(path.read_text()))
        except OSError as error:
            # В кадре write_text лежит весь новый текст (`data`): pytest -l и unittest --locals напечатали бы его вместе с ключом API.
            raise RuntimeError(f"edit could not rewrite {path.name}: {type(error).__name__}") from None
        # Хеши вместо текстов: провал assertNotEqual напечатал бы оба текста, а в opencode.jsonc лежит ключ API.
        self.assertTrue(text_digest(path) != before, f"edit changed nothing in {path.name}")  # иначе тест ничего не проверяет

    def check(self, root: Path, code: int) -> list[str]:
        """Run the script on `root`, assert the exit code, and return the problem lines."""
        done = subprocess.run([sys.executable, str(SCRIPT_PATH), str(root)], capture_output=True, text=True)
        self.assertEqual(done.returncode, code, done.stdout)
        return problem_lines(done.stdout)

    def test_real_configs_have_no_problems(self) -> None:
        self.assertEqual(self.check(make_root(self.root, set()), 0), [])

    def test_edit_that_changes_nothing_fails_without_file_text(self) -> None:
        jsonc = copy_jsonc(make_root(self.root, set()))
        with self.assertRaises(AssertionError) as caught:
            self.edit(jsonc, lambda t: t)
        # Порядок важен: assertTrue с `in` напечатал бы сообщение, если бы в нём был текст конфига.
        self.assertFalse(leaks_file_text(caught.exception, jsonc), f"the message repeats text of {jsonc.name}")
        self.assertTrue(jsonc.name in str(caught.exception), "the message does not name the file")

    def test_write_error_in_edit_keeps_file_text_out_of_traceback(self) -> None:
        jsonc = copy_jsonc(make_root(self.root, set()))
        jsonc.chmod(0o444)
        # ignore_errors=True в setUp скрыл бы сбой rmtree, и копия конфига с ключом осталась бы во временном каталоге.
        self.addCleanup(jsonc.chmod, 0o644)
        try:
            self.edit(jsonc, lambda t: t + " ")
        except Exception as error:  # assertRaises очищает кадры traceback, а проверка читает их локальные переменные
            leaked = leaks_file_text(error, jsonc)
            diagnosed = jsonc.name in str(error) and "PermissionError" in str(error)
        else:
            self.fail(f"edit did not fail on the read-only {jsonc.name}")
        # Проверки стоят вне except: иначе упавший assert потянул бы за собой цепочку с текстом конфига.
        self.assertFalse(leaked, f"the error repeats text of {jsonc.name}")
        self.assertTrue(diagnosed, f"the error does not name {jsonc.name} and the error type")

    def test_changed_line_in_constraints_names_the_section(self) -> None:
        root = make_root(self.root, {HOME / ".claude/agents"})
        self.edit(root / ".claude/agents/code-writer-t2.md", lambda t: change_first_line(t, "## Constraints"))
        self.assertEqual(self.check(root, 1), ["claude: section `Constraints` of code-writer-t2 differs from code-writer-t1"])

    def test_changed_line_in_workflow_is_not_a_problem(self) -> None:
        root = make_root(self.root, {HOME / ".claude/agents"})
        self.edit(root / ".claude/agents/code-writer-t2.md", lambda t: change_first_line(t, "## Workflow"))
        self.assertEqual(self.check(root, 0), [])

    def test_removed_verification_heading_in_codex_t1_is_a_contract_problem(self) -> None:
        root = make_root(self.root, {HOME / ".codex/agents"})
        self.edit(root / ".codex/agents/code-writer-t1.toml", lambda t: t.replace("### Verification\n", "", 1))
        self.assertEqual(self.check(root, 1), ["codex: code-writer-t1 report contract ### headings differ from claude"])

    def test_removed_bug_fixes_section_in_opencode_t3_is_a_problem(self) -> None:
        root = make_root(self.root, {HOME / ".config/opencode/agent"})
        self.edit(root / ".config/opencode/agent/code-writer-t3.md", lambda t: drop_section(t, "## Bug fixes"))
        self.assertIn("opencode: section `Bug fixes` missing in code-writer-t3", self.check(root, 1))

    def test_heading_inside_a_code_fence_is_not_a_section(self) -> None:
        root = make_root(self.root, {HOME / ".claude/agents"})
        fenced = "## Workflow\n\n```\n## Constraints\n```\n"
        self.edit(root / ".claude/agents/code-writer-t1.md", lambda t: t.replace("## Workflow\n", fenced, 1))
        self.assertEqual(self.check(root, 0), [])

    def test_logical_persona_path_in_jsonc_is_accepted(self) -> None:
        root = make_root(self.root, set())
        jsonc = copy_jsonc(root)
        self.edit(jsonc, lambda t: t.replace(str(HOME / ".claude/persona.md"), str(root / ".claude/persona.md")))
        self.assertEqual(self.check(root, 0), [])

    def root_without_persona(self) -> Path:
        """Return a ROOT whose opencode.jsonc names neither the logical nor the resolved persona path."""
        root = make_root(self.root, set())
        self.edit(copy_jsonc(root), replace_persona_forms)
        return root

    def test_persona_path_missing_from_jsonc_is_a_problem(self) -> None:
        root = self.root_without_persona()
        self.assertEqual(self.check(root, 1), ["opencode instructions do not include persona.md"])

    def test_relative_root_dot_reports_missing_persona_path(self) -> None:
        root = self.root_without_persona()
        # В check() нет cwd, поэтому скрипт запускается здесь напрямую.
        done = subprocess.run([sys.executable, str(SCRIPT_PATH), "."], cwd=root, capture_output=True, text=True)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(problem_lines(done.stdout), ["opencode instructions do not include persona.md"])

    def test_backup_copy_of_persona_path_in_instructions_is_a_problem(self) -> None:
        root = make_root(self.root, set())
        backup = str(root / ".claude/persona.md") + ".bak"
        self.edit(copy_jsonc(root), lambda t: t.replace(str(HOME / ".claude/persona.md"), backup))
        self.assertEqual(self.check(root, 1), ["opencode instructions do not include persona.md"])

    def test_prefixed_persona_path_in_instructions_is_a_problem(self) -> None:
        root = make_root(self.root, set())
        prefixed = "/backup" + str(root / ".claude/persona.md")
        self.edit(copy_jsonc(root), lambda t: t.replace(str(HOME / ".claude/persona.md"), prefixed))
        self.assertEqual(self.check(root, 1), ["opencode instructions do not include persona.md"])

    def test_persona_path_outside_instructions_is_a_problem(self) -> None:
        root = self.root_without_persona()
        note = str(root / ".claude/persona.md")
        jsonc = root / ".config/opencode/opencode.jsonc"
        self.edit(jsonc, lambda t: t.replace('"instructions"', f'"note": "{note}", "instructions"', 1))
        self.assertEqual(self.check(root, 1), ["opencode instructions do not include persona.md"])

    def test_unparsable_jsonc_is_a_problem(self) -> None:
        root = make_root(self.root, set())
        self.edit(copy_jsonc(root), lambda t: t.rstrip()[:-1])  # без закрывающей скобки верхнего уровня
        # Пустой allowlist добавляет строки `not available`, как и при отсутствии блока skill; здесь важны строки о jsonc.
        config_lines = [p for p in self.check(root, 1) if p.startswith(("opencode.jsonc", "opencode instructions"))]
        self.assertEqual(config_lines, ["opencode.jsonc does not parse as JSON"])

    def test_jsonc_without_instructions_list_is_a_problem(self) -> None:
        root = make_root(self.root, set())
        self.edit(copy_jsonc(root), lambda t: t.replace('"instructions"', '"instruction"', 1))
        self.assertEqual(self.check(root, 1), ["opencode.jsonc has no instructions list"])

    def test_jsonc_without_skill_block_is_a_problem(self) -> None:
        root = make_root(self.root, set())
        self.edit(copy_jsonc(root), lambda _: "[]")
        done = subprocess.run([sys.executable, str(SCRIPT_PATH), str(root)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(done.stderr, "")  # Падение скрипта дало бы Traceback в stderr.
        problems = problem_lines(done.stdout)
        self.assertIn("opencode.jsonc has no skill allowlist", problems)
        self.assertIn("opencode.jsonc has no instructions list", problems)

    def test_skill_block_without_space_after_colon_is_accepted(self) -> None:
        root = make_root(self.root, set())
        # Пробел после двоеточия не значим для JSON: блок должен находиться и без него.
        self.edit(copy_jsonc(root), lambda t: t.replace('"skill": {', '"skill":{', 1))
        self.assertEqual(self.check(root, 0), [])

    def test_skill_text_in_comment_is_not_an_allowlist(self) -> None:
        root = make_root(self.root, set())
        # Текст в комментарии не должен заменять настоящий блок; настоящий блок переименован.
        self.edit(copy_jsonc(root), lambda t: '// "skill": {\n' + t.replace('"skill": {', '"skill_renamed": {', 1))
        self.assertIn("opencode.jsonc has no skill allowlist", self.check(root, 1))


if __name__ == "__main__":
    unittest.main()
