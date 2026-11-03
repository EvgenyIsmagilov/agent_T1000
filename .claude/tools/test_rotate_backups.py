#!/usr/bin/env python3
"""Regression tests for rotate-backups.py, the deleter of old harness backups.

Every test builds its own `.harness-backups` directory under the system temp dir
and passes its own `now`. The real backup directory is never touched.
"""
import contextlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

SCRIPT_PATH = Path(__file__).resolve().parent / "rotate-backups.py"
DAY = 86400
# Целая секунда в прошлом: нетронутые файлы выглядят свежими, а set_age задаёт возраст точно.
NOW = 1_700_000_000.0


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("rotate_backups", SCRIPT_PATH)
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


ROTATE = load_script()


def set_age(path: Path, seconds: int) -> None:
    """Set the mtime of the path itself (a link is not followed) to `seconds` before NOW."""
    stamp = NOW - seconds
    os.utime(path, (stamp, stamp), follow_symlinks=False)


def write(path: Path, age: int, text: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    set_age(path, age)
    return path


class RotateBackupsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.backups = self.tmp / ".harness-backups"
        self.backups.mkdir()

    def run_main(
        self, *args: str, directory: Path | str | None = None, now: float = NOW
    ) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        argv = ["--dir", str(directory or self.backups), *args]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = ROTATE.main(argv, now=now)
            except SystemExit as exc:  # так argparse сообщает о неверных аргументах
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_refuses_directory_with_wrong_name(self) -> None:
        # Последний пример: pathlib не схлопывает "..", имя компонента "..", а сам путь — это self.tmp.
        wrong = [self.tmp / "backups", self.tmp / ".harness-backups-old", self.backups / ".."]
        for directory in wrong:
            directory.mkdir(exist_ok=True)
            old = write(directory / "old.txt", 30 * DAY)
            with self.subTest(directory=str(directory)):
                code, _, err = self.run_main("--apply", directory=directory)
                self.assertEqual(code, 2)
                self.assertIn("refused", err)
                self.assertTrue(old.exists())

    def test_refuses_symlinked_directory(self) -> None:
        real = self.tmp / "real"
        old = write(real / "old.txt", 30 * DAY)
        link = self.tmp / "elsewhere" / ".harness-backups"
        link.parent.mkdir()
        link.symlink_to(real)
        # Со слэшем в конце ОС сама разыменовала бы ссылку; pathlib слэш отбрасывает.
        spellings: list[Path | str] = [link, f"{link}/"]
        for directory in spellings:
            with self.subTest(directory=str(directory)):
                code, _, err = self.run_main("--apply", directory=directory)
                self.assertEqual(code, 2)
                self.assertIn("symlink", err)
                self.assertTrue(old.exists())

    def test_refuses_a_file_in_place_of_the_directory(self) -> None:
        impostor = write(self.tmp / "holder" / ".harness-backups", 30 * DAY)

        code, _, err = self.run_main("--apply", directory=impostor)

        self.assertEqual(code, 2)
        self.assertIn("not a directory", err)
        self.assertTrue(impostor.exists())

    def test_missing_directory_is_not_an_error(self) -> None:
        missing = self.tmp / "none" / ".harness-backups"
        code, out, _ = self.run_main("--apply", directory=missing)
        self.assertEqual(code, 0)
        self.assertIn("does not exist", out)
        self.assertFalse(missing.parent.exists())

    def test_rejects_days_below_one(self) -> None:
        old = write(self.backups / "old.txt", 30 * DAY)
        for days in ("0", "-3", "abc"):
            with self.subTest(days=days):
                code, _, _ = self.run_main("--apply", "--days", days)
                self.assertEqual(code, 2)
                self.assertTrue(old.exists())

    def test_dry_run_lists_old_entries_and_deletes_nothing(self) -> None:
        write(self.backups / "old.txt", 15 * DAY, "12345")
        write(self.backups / "old-folder" / "big.txt", 20 * DAY, "x" * 2048)
        set_age(self.backups / "old-folder", 20 * DAY)
        write(self.backups / "fresh.txt", 1 * DAY)

        code, out, err = self.run_main()

        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(os.listdir(self.backups)), ["fresh.txt", "old-folder", "old.txt"])
        self.assertEqual(out.splitlines(), [
            "would delete  old-folder  20.0d  2.0K",
            "would delete  old.txt  15.0d  5B",
            "summary: would delete 2 of 3 entries (2.0K), kept 1, errors 0 (dry-run, pass --apply to delete)",
        ])

    def test_entry_exactly_at_the_limit_stays_one_second_older_goes(self) -> None:
        at_limit = write(self.backups / "at-limit.txt", 14 * DAY)
        older = write(self.backups / "older.txt", 14 * DAY + 1)

        code, out, err = self.run_main("--apply")

        self.assertEqual(code, 0, err)
        self.assertTrue(at_limit.exists())
        self.assertFalse(older.exists())
        self.assertEqual(out.splitlines(), [
            "deleted older.txt",
            "summary: deleted 1 of 2 entries (4B), kept 1, errors 0",
        ])

    def test_days_option_sets_the_limit(self) -> None:
        stays = write(self.backups / "twenty-days.txt", 20 * DAY)  # при лимите по умолчанию (14) он бы ушёл
        goes = write(self.backups / "thirty-one-days.txt", 31 * DAY)

        code, _, err = self.run_main("--apply", "--days", "30")

        self.assertEqual(code, 0, err)
        self.assertTrue(stays.exists())
        self.assertFalse(goes.exists())

    def test_folder_stays_whole_while_anything_in_it_is_fresh(self) -> None:
        mixed = self.backups / "mixed"
        write(mixed / "old.txt", 30 * DAY)
        write(mixed / "sub" / "fresh.txt", 1 * DAY)  # свежий файл на втором уровне
        set_age(mixed / "sub", 30 * DAY)
        set_age(mixed, 30 * DAY)
        touched = self.backups / "touched"  # все файлы старые, свежа только сама папка
        write(touched / "old.txt", 30 * DAY)
        set_age(touched, 1 * DAY)

        code, out, err = self.run_main("--apply")

        self.assertEqual(code, 0, err)
        self.assertTrue((mixed / "old.txt").exists())
        self.assertTrue((mixed / "sub" / "fresh.txt").exists())
        self.assertTrue((touched / "old.txt").exists())
        self.assertEqual(out.splitlines(), ["summary: deleted 0 of 2 entries (0B), kept 2, errors 0"])

    def test_folder_stays_whole_while_a_nested_folder_is_fresh(self) -> None:
        folder = self.backups / "old-folder"
        write(folder / "old.txt", 30 * DAY)
        (folder / "empty-sub").mkdir()  # единственная свежая запись: пустая папка внутри
        set_age(folder / "empty-sub", 1 * DAY)
        set_age(folder, 30 * DAY)

        code, out, err = self.run_main("--apply")

        self.assertEqual(code, 0, err)
        self.assertTrue((folder / "old.txt").exists())
        self.assertEqual(out.splitlines(), ["summary: deleted 0 of 1 entries (0B), kept 1, errors 0"])

    def test_folder_with_only_old_files_goes(self) -> None:
        stale = self.backups / "stale"
        write(stale / "a.txt", 30 * DAY)
        write(stale / "sub" / "b.txt", 20 * DAY)
        set_age(stale / "sub", 20 * DAY)
        set_age(stale, 30 * DAY)

        code, _, err = self.run_main("--apply")

        self.assertEqual(code, 0, err)
        self.assertFalse(stale.exists())
        self.assertEqual(os.listdir(self.backups), [])  # сам каталог бэкапов остаётся

    def test_hidden_entries_are_rotated_like_any_other(self) -> None:
        old_folder = write(self.backups / ".claude" / "settings.json", 30 * DAY).parent
        set_age(old_folder, 30 * DAY)
        old_file = write(self.backups / ".old-dotfile", 30 * DAY)
        fresh = write(self.backups / ".fresh-dotfile", 1 * DAY)

        code, _, err = self.run_main("--apply")

        self.assertEqual(code, 0, err)
        self.assertFalse(old_folder.exists())
        self.assertFalse(old_file.exists())
        self.assertTrue(fresh.exists())

    def test_symlink_entry_is_judged_by_the_link_and_removed_as_a_link(self) -> None:
        fresh_target = self.tmp / "fresh-target"
        fresh_kept = write(fresh_target / "keep.txt", 0, "precious")
        old_link = self.backups / "old-link"  # ссылка старая, хотя за ней свежие файлы
        old_link.symlink_to(fresh_target)
        set_age(old_link, 30 * DAY)
        old_target = self.tmp / "old-target"
        old_kept = write(old_target / "keep.txt", 30 * DAY, "precious")
        set_age(old_target, 30 * DAY)
        fresh_link = self.backups / "fresh-link"  # ссылка свежая, хотя цель старая
        fresh_link.symlink_to(old_target)
        set_age(fresh_link, 1 * DAY)

        code, out, err = self.run_main("--apply")

        self.assertEqual(code, 0, err)
        self.assertIn("deleted old-link", out.splitlines())
        self.assertFalse(os.path.lexists(old_link))
        self.assertTrue(os.path.lexists(fresh_link))
        self.assertEqual(fresh_kept.read_text(), "precious")
        self.assertEqual(old_kept.read_text(), "precious")

    def test_symlink_inside_an_old_folder_is_not_followed(self) -> None:
        outside = self.tmp / "outside"
        kept = write(outside / "keep.txt", 0, "precious")  # свежий файл за ссылкой
        folder = self.backups / "old-folder"
        write(folder / "a.txt", 30 * DAY)
        (folder / "link").symlink_to(outside)
        set_age(folder / "link", 30 * DAY)
        set_age(folder, 30 * DAY)

        code, _, err = self.run_main("--apply")

        self.assertEqual(code, 0, err)
        self.assertFalse(folder.exists())
        self.assertEqual(kept.read_text(), "precious")

    def test_folder_stays_whole_while_a_nested_link_is_fresh(self) -> None:
        old_target = self.tmp / "old-target"  # цель старая: файлы по ссылке не должны держать папку
        write(old_target / "keep.txt", 30 * DAY)
        set_age(old_target, 30 * DAY)
        folder = self.backups / "old-folder"
        write(folder / "old.txt", 30 * DAY)
        (folder / "link").symlink_to(old_target)
        set_age(folder / "link", 1 * DAY)  # единственная свежая запись: сама ссылка
        set_age(folder, 30 * DAY)

        code, out, err = self.run_main("--apply")

        self.assertEqual(code, 0, err)
        self.assertTrue((folder / "old.txt").exists())
        self.assertTrue((folder / "link").is_symlink())
        self.assertEqual(out.splitlines(), ["summary: deleted 0 of 1 entries (0B), kept 1, errors 0"])

    # Настоящие права вместо подмены os.unlink: тест не зависит от того, чем скрипт удаляет.
    @unittest.skipIf(os.geteuid() == 0, "root ignores permission bits")
    def test_failed_deletion_is_reported_and_other_entries_still_go(self) -> None:
        locked = self.backups / "a-locked"  # в порядке обработки идёт раньше остальных
        write(locked / "x.txt", 30 * DAY)
        set_age(locked, 30 * DAY)
        locked.chmod(0o500)  # в папке без права записи файл не удалить
        self.addCleanup(locked.chmod, 0o700)
        later = write(self.backups / "b-old.txt", 30 * DAY)

        code, out, err = self.run_main("--apply")

        self.assertEqual(code, 1)
        self.assertIn("a-locked", err)
        self.assertTrue((locked / "x.txt").exists())
        self.assertFalse(later.exists())
        self.assertEqual(out.splitlines(), [
            "deleted b-old.txt",
            "summary: deleted 1 of 2 entries (4B), kept 0, errors 1",
        ])

    @unittest.skipIf(os.geteuid() == 0, "root ignores permission bits")
    def test_entry_with_an_unreadable_folder_is_kept_and_reported(self) -> None:
        folder = self.backups / "half-visible"
        old = write(folder / "old.txt", 30 * DAY)
        sealed = folder / "sealed"
        write(sealed / "fresh.txt", 1 * DAY)  # после chmod 0 этот свежий файл не виден
        set_age(sealed, 30 * DAY)
        set_age(folder, 30 * DAY)
        sealed.chmod(0)
        self.addCleanup(sealed.chmod, 0o700)

        for args in ((), ("--apply",)):
            with self.subTest(args=args):
                code, out, err = self.run_main(*args)
                self.assertEqual(code, 1)
                self.assertIn("half-visible", err)
                self.assertNotIn("half-visible", out)
                self.assertTrue(old.exists())


if __name__ == "__main__":
    unittest.main()
