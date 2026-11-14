#!/usr/bin/env python3
"""Delete harness backups that are older than N days.

Policy: backups of harness configs live in ~/.harness-backups
and are rotated after 14 days. A deleted backup cannot be restored, so the
script does a dry-run unless you pass --apply.

The unit of rotation is one top-level entry of the directory: a file, a folder
or a symlink. The age of an entry is the newest mtime of the entry and of
everything inside it. A folder with one fresh file stays whole. An entry is
deleted only when it is strictly older than N days.

Symlinks are never followed. A link is judged by its own mtime and is removed
as a link. Its target is not read and not changed.

The script refuses (exit code 2) a directory that is not named .harness-backups,
a symlink, or a path that is not a directory. A missing directory is not an
error. If an entry cannot be read or deleted, the script reports it, goes on
with the other entries, and exits with code 1.

Output: one line per entry that goes ("would delete  NAME  AGEd  SIZE" in a
dry-run, "deleted NAME" with --apply), then one summary line. Errors go to stderr.

stdlib only. Usage:
    python3 ~/.claude/tools/rotate-backups.py [--dir PATH] [--days N] [--apply]
"""
import argparse
import os
import shutil
import stat
import sys
import time
from pathlib import Path

BACKUP_DIR_NAME = ".harness-backups"
DEFAULT_DIR = Path.home() / BACKUP_DIR_NAME
DEFAULT_DAYS = 14
SECONDS_PER_DAY = 86400


def human_size(size: float) -> str:
    for unit in ("B", "K", "M"):
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}G"


def raise_error(error: OSError) -> None:
    raise error


def scan(entry: str) -> tuple[float, int]:
    """Return the newest mtime and the total size of an entry and everything inside it.

    Only lstat is used and os.walk does not follow links, so a symlink is measured as a link.
    A folder that cannot be read raises OSError: unknown content must not make an entry look old.
    """
    st = os.lstat(entry)
    if not stat.S_ISDIR(st.st_mode):
        return st.st_mtime, st.st_size
    newest, size = st.st_mtime, 0
    for dirpath, dirnames, filenames in os.walk(entry, onerror=raise_error, followlinks=False):
        for name in dirnames + filenames:
            st = os.lstat(os.path.join(dirpath, name))
            newest = max(newest, st.st_mtime)
            if not stat.S_ISDIR(st.st_mode):
                size += st.st_size
    return newest, size


def remove(path: str) -> None:
    """Delete a file, a symlink (only the link) or a directory tree."""
    if stat.S_ISDIR(os.lstat(path).st_mode):
        shutil.rmtree(path)
    else:
        os.unlink(path)


def positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be an integer >= 1, got {value}")
    return value


def refusal(backup_dir: Path) -> str | None:
    """Return why the directory must not be rotated, or None when it is safe."""
    if backup_dir.name != BACKUP_DIR_NAME:
        return f"its name is not {BACKUP_DIR_NAME}"
    if backup_dir.is_symlink():
        return "it is a symlink"
    if backup_dir.exists() and not backup_dir.is_dir():
        return "it is not a directory"
    return None


def main(argv: list[str] | None = None, now: float | None = None) -> int:
    ap = argparse.ArgumentParser(description="Delete harness backups older than N days (dry-run by default)")
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR,
                    help=f"backup directory, its name must be {BACKUP_DIR_NAME} (default: %(default)s)")
    ap.add_argument("--days", type=positive_int, default=DEFAULT_DAYS,
                    help="delete entries older than this many days (default: %(default)s)")
    ap.add_argument("--apply", action="store_true", help="really delete; without it nothing is deleted")
    args = ap.parse_args(argv)

    reason = refusal(args.dir)
    if reason:
        print(f"refused: {args.dir}: {reason}", file=sys.stderr)
        return 2
    if not args.dir.exists():
        print(f"{args.dir} does not exist, nothing to do")
        return 0

    now = time.time() if now is None else now  # единственное обращение к часам
    limit = args.days * SECONDS_PER_DAY
    names = sorted(os.listdir(args.dir))  # listdir отдаёт и скрытые записи, но не "." и ".."
    done = kept = failed = freed = 0
    for name in names:
        path = os.path.join(args.dir, name)
        try:  # в try только работа с файлами: сбой print не должен выглядеть сбоем удаления
            newest, size = scan(path)
            age = now - newest
            expired = age > limit  # строго больше: запись ровно на границе остаётся
            if expired and args.apply:
                remove(path)
        except OSError as exc:
            print(f"error {name}: {exc}", file=sys.stderr)
            failed += 1
            continue  # остальные записи обрабатываются дальше
        if not expired:
            kept += 1
            continue
        if args.apply:
            print(f"deleted {name}")
        else:
            print(f"would delete  {name}  {age / SECONDS_PER_DAY:.1f}d  {human_size(size)}")
        done += 1
        freed += size
    verb = "deleted" if args.apply else "would delete"
    note = "" if args.apply else " (dry-run, pass --apply to delete)"
    print(f"summary: {verb} {done} of {len(names)} entries ({human_size(freed)}), kept {kept}, errors {failed}{note}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
