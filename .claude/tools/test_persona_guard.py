#!/usr/bin/env python3
"""Regression tests for persona-guard.py and its git hook wrappers.

Dummy persona data only, in throwaway repos under the system temp dir.
"""
import os
import shutil
import subprocess
import tempfile
import unicodedata
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
GUARD = TOOLS / "persona-guard.py"
PLINE = "Это длинный тайный фиктивный — текст персоны для теста"
MARK = "zzmarkerzz"
UNRELATED = "def f(x):\n    return x + 1\n"


def run(cwd, *args, env=None, input=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, input=input, env=env)


def text(r):
    return r.stdout.decode() + r.stderr.decode()


class GuardCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "root"
        (self.root / ".claude").mkdir(parents=True)
        self.write_root(f"# t\n- short\n- {PLINE}\n", f"{MARK}\n\n")
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        for c in (["init", "-q"], ["config", "user.email", "a@b"], ["config", "user.name", "x"],
                  ["config", "commit.gpgsign", "false"]):
            self.git(*c)
        (self.repo / "base.txt").write_text("base\n")
        self.git("add", ".")
        self.git("commit", "-qm", "init")

    def write_root(self, persona, markers):
        (self.root / ".claude/persona.md").write_text(persona, encoding="utf-8")
        (self.root / ".claude/persona-markers.txt").write_text(markers, encoding="utf-8")

    def git(self, *args, **kw):
        return run(self.repo, "git", *args, **kw)

    def stage(self, name, content):
        p = self.repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        self.git("add", name)

    def guard(self, *extra, root=None, input=None):
        return run(self.repo, "python3", str(GUARD), "--root", str(root or self.root), *extra, input=input)

    def assertBlocked(self, r, needle=""):
        self.assertEqual(r.returncode, 1, text(r))
        self.assertIn(needle, r.stdout.decode())
        self.assertNotIn(MARK, text(r))
        self.assertNotIn("тайный", text(r))

    def assertClean(self, r):
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, b"", b""))


class StagedTests(GuardCase):
    def test_clean(self):
        self.stage("a.py", UNRELATED)
        self.assertClean(self.guard())

    def test_marker_case_insensitive(self):
        self.stage("a.txt", f"x {MARK.upper()} y\n")
        r = self.guard()
        self.assertBlocked(r, "blocked: a.txt: marker #1")

    def test_persona_line_forms(self):
        half = len(PLINE) // 2
        forms = {
            "exact": PLINE,
            "embedded": f"pre {PLINE} post",
            "upper": PLINE.upper(),
            "wrapped": PLINE[:half] + "\n" + PLINE[half:],
            "wrapped_indent": PLINE[:half] + "\n    # " + PLINE[half:],
            "nbsp": PLINE.replace(" ", " "),
            "en_dash": PLINE.replace("—", "–"),
            "hyphen": PLINE.replace("—", "-"),
            "nfd": unicodedata.normalize("NFD", PLINE),
            "u_escapes": PLINE.encode("ascii", "backslashreplace").decode(),
            "concat": f'x = "{PLINE[:half]}" "{PLINE[half:]}"',
        }
        for name, s in forms.items():
            with self.subTest(name):
                self.stage("a.txt", s + "\n")
                self.assertBlocked(self.guard(), "persona-line #3")

    def test_other_encodings(self):
        forms = {
            "utf16": PLINE.encode("utf-16"),
            "utf16be": PLINE.encode("utf-16-be"),
            "cp1251": PLINE.replace("—", "-").encode("cp1251"),
            "nul": b"\x00" + PLINE.encode("utf-8"),
        }
        for name, b in forms.items():
            with self.subTest(name):
                self.stage("a.dat", b)
                self.assertBlocked(self.guard(), "a.dat")

    def test_gitattributes_binary_and_textconv(self):
        self.stage(".gitattributes", "*.dat -diff\n*.bin binary\n*.cv diff=x\n")
        self.git("config", "diff.x.textconv", "true")
        for n in ("a.dat", "a.bin", "a.cv"):
            with self.subTest(n):
                self.stage(n, PLINE + "\n")
                self.assertBlocked(self.guard(), n)

    def test_type_change_to_symlink(self):
        (self.repo / "base.txt").unlink()
        os.symlink(PLINE, self.repo / "base.txt")
        self.git("add", "base.txt")
        self.assertBlocked(self.guard(), "base.txt")

    def test_filename_variants(self):
        for n in ("sub/persona.md", "Persona.md", "persona.md.bak", "persona-markers.txt"):
            with self.subTest(n):
                self.stage(n, "innocent\n")
                self.assertBlocked(self.guard(), "filename")
                self.git("rm", "-qf", "--cached", n)
        self.stage("personality.md", "innocent\n")
        self.assertClean(self.guard())

    def test_plus_prefixed_content(self):
        self.stage("a.txt", f"++ {MARK}\n")
        self.assertBlocked(self.guard())
        self.stage("b.txt", f"++ b/evil\n{MARK}\n")
        self.assertBlocked(self.guard(), "b.txt")

    def test_rename_with_edit(self):
        (self.repo / "base.txt").write_text("base\n" * 20)
        self.git("add", ".")
        self.git("commit", "-qm", "m")
        self.git("mv", "base.txt", "new.txt")
        with open(self.repo / "new.txt", "a") as f:
            f.write(f"{MARK}\n")
        self.git("add", ".")
        self.assertBlocked(self.guard(), "new.txt")

    def test_removed_and_unstaged_lines_do_not_block(self):
        (self.repo / "base.txt").write_text(PLINE + "\n")
        self.git("add", ".")
        self.git("commit", "-qm", "p", "--no-verify")
        self.stage("base.txt", "other\n")
        self.assertClean(self.guard())
        self.git("commit", "-qm", "q")
        (self.repo / "base.txt").write_text(f"{MARK}\n")
        self.assertClean(self.guard())

    def test_reported_path_and_index(self):
        self.git("config", "diff.noprefix", "true")
        self.stage("дир/файл с пробелом.txt", f"{MARK}\n")
        r = self.guard()
        self.assertEqual(r.stdout.decode(), "blocked: дир/файл с пробелом.txt: marker #1\n")
        self.stage("q\"uote.txt", PLINE + "\n")
        self.assertIn('blocked: q"uote.txt: persona-line #3\n', self.guard().stdout.decode())

    def test_binary_does_not_crash(self):
        self.stage("b.bin", bytes(range(256)) * 4 + b"\xff\xfe\x00")
        self.assertClean(self.guard())


class FailClosedTests(GuardCase):
    def test_missing_files(self):
        for n in ("persona.md", "persona-markers.txt"):
            p = self.root / ".claude" / n
            p.rename(p.with_name(n + "x"))
            self.assertEqual(self.guard().returncode, 2)
            p.with_name(n + "x").rename(p)

    def test_empty_fingerprints(self):
        for persona, markers in (("", f"{MARK}\n"), ("# t\n- short\n", f"{MARK}\n"),
                                 (f"# t\n- {PLINE}\n", ""), (f"# t\n- {PLINE}\n", "\n  \n")):
            with self.subTest((persona, markers)):
                self.write_root(persona, markers)
                self.stage("a.py", UNRELATED)
                self.assertEqual(self.guard().returncode, 2)

    def test_not_a_repo(self):
        r = run(self.tmp, "python3", str(GUARD), "--root", str(self.root))
        self.assertEqual(r.returncode, 2)


class HookTests(GuardCase):
    def setUp(self):
        super().setUp()
        tools = self.tmp / "tools"
        shutil.copytree(TOOLS / "githooks", tools / "githooks")
        for n in ("persona-guard.py", "persona_scan.py"):
            shutil.copy(TOOLS / n, tools / n)
        for h in (tools / "githooks").iterdir():
            h.chmod(0o755)
        self.git("config", "core.hooksPath", str(tools / "githooks"))
        self.env = {**os.environ, "PERSONA_GUARD_ROOT": str(self.root)}
        self.remote = self.tmp / "remote.git"
        run(self.tmp, "git", "init", "-q", "--bare", str(self.remote))
        self.git("remote", "add", "origin", str(self.remote))
        self.branch = self.git("branch", "--show-current").stdout.decode().strip()
        self.assertEqual(self.git("push", "-q", "origin", self.branch, env=self.env).returncode, 0)

    def remote_head(self):
        return run(self.remote, "git", "rev-parse", "HEAD").stdout

    def test_hooks_syntax(self):
        for h in (TOOLS / "githooks").iterdir():
            self.assertEqual(run(self.tmp, "sh", "-n", str(h)).returncode, 0, h.name)

    def test_annotated_tag_message_refused_on_push(self):
        self.git("tag", "-a", "v1", "-m", PLINE)
        self.git("tag", "-a", "v2", "-m", "clean outer", "v1")
        for name in ("v1", "v2"):
            with self.subTest(name):
                r = self.git("push", "origin", name, env=self.env)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(f"blocked: refs/tags/{name}: persona-line #3", text(r))
                self.assertNotIn("тайный", text(r))
                self.assertEqual(run(self.remote, "git", "tag", "-l").stdout, b"")
        self.git("tag", "-a", "v3", "-m", "clean")
        self.assertEqual(self.git("push", "origin", "v3", env=self.env).returncode, 0)

    def test_force_push_unknown_remote_sha(self):
        other = self.tmp / "other"
        run(self.tmp, "git", "clone", "-q", str(self.remote), str(other))
        for c in (["config", "user.email", "a@b"], ["config", "user.name", "x"],
                  ["config", "commit.gpgsign", "false"]):
            run(other, "git", *c)
        (other / "o.txt").write_text("o\n")
        run(other, "git", "add", ".")
        run(other, "git", "commit", "-qm", "ahead", "--no-verify")
        self.assertEqual(run(other, "git", "push", "-q", "origin", self.branch, env=self.env).returncode, 0)
        # Устаревший клон: удалённый sha ему неизвестен.
        self.stage("p.txt", f"{MARK}\n")
        self.git("commit", "-qm", "local", "--no-verify")
        r = self.git("push", "--force", "origin", self.branch, env=self.env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("blocked:", text(r))
        self.assertNotIn("128", text(r))
        self.git("reset", "-q", "--hard", "HEAD~1")
        self.stage("c.py", UNRELATED)
        self.git("commit", "-qm", "clean")
        r = self.git("push", "--force", "origin", self.branch, env=self.env)
        self.assertEqual(r.returncode, 0, text(r))

    def test_cherry_picked_persona_commit_refused_on_push(self):
        self.git("checkout", "-qb", "side")
        self.stage("p.txt", PLINE + "\n")
        self.assertEqual(self.git("commit", "-qm", "side", "--no-verify").returncode, 0)
        sha = self.git("rev-parse", "HEAD").stdout.decode().strip()
        self.git("checkout", "-q", self.branch)
        self.assertEqual(self.git("cherry-pick", sha, env=self.env).returncode, 0)
        before = self.remote_head()
        r = self.git("push", "origin", self.branch, env=self.env)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.remote_head(), before)
        self.assertIn("blocked:", text(r))
        self.assertNotIn("тайный", text(r))

    def test_added_then_removed_commit_still_refused(self):
        self.stage("p.txt", PLINE + "\n")
        self.git("commit", "-qm", "a", "--no-verify")
        self.git("rm", "-q", "p.txt")
        self.git("commit", "-qm", "b", "--no-verify")
        self.assertNotEqual(self.git("push", "origin", self.branch, env=self.env).returncode, 0)

    def test_new_branch_refused_and_clean_push_ok(self):
        self.git("checkout", "-qb", "feat")
        self.stage("p.txt", f"{MARK}\n")
        self.git("commit", "-qm", "a", "--no-verify")
        self.assertNotEqual(self.git("push", "origin", "feat", env=self.env).returncode, 0)
        self.git("reset", "-q", "--hard", "HEAD~1")
        self.stage("c.py", UNRELATED)
        self.assertEqual(self.git("commit", "-qm", "clean", env=self.env).returncode, 0)
        self.assertEqual(self.git("push", "origin", "feat", env=self.env).returncode, 0)

    def test_delete_branch_skipped(self):
        self.git("push", "-q", "origin", f"{self.branch}:tmp", env=self.env)
        self.assertEqual(self.git("push", "origin", ":tmp", env=self.env).returncode, 0)



if __name__ == "__main__":
    unittest.main()
