"""Git hook guard: block commits, messages and pushes that contain personal persona text.

Modes: staged diff (manual check, default), --pre-push (pre-push hook; reads ref lines
from stdin, scans commits, their messages and annotated-tag messages). Exit 1 = hit,
exit 2 = guard failure.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from persona_scan import ScanError, find_hits, load_fingerprints

BLOCKED_PREFIXES = ("persona.md", "persona-markers")
HUNK = re.compile(rb"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
GIT_DIFF = ["--literal-pathspecs", "diff", "--no-color", "--no-renames"]


def git(*args: str) -> bytes:
  try:
    res = subprocess.run(["git", *args], capture_output=True)
  except OSError as e:
    raise ScanError(f"cannot run git: {e.strerror}")
  if res.returncode != 0:
    raise ScanError(f"git command failed with code {res.returncode}")
  return res.stdout


def added_bytes(diff: bytes) -> bytes:
  # Добавленные строки одного файла без префикса "+". Границы hunk берём из
  # счётчиков в заголовке @@, а не по префиксу "+++": добавленная строка
  # "++ x" в диффе выглядит как "+++ x" и иначе была бы принята за заголовок.
  out = []
  old_left = new_left = 0
  for line in diff.split(b"\n"):
    if old_left or new_left:
      if line.startswith(b"+"):
        new_left -= 1
        out.append(line[1:])
      elif line.startswith(b"-"):
        old_left -= 1
      continue
    m = HUNK.match(line)
    if m:
      old_left = int(m.group(1) or 1)
      new_left = int(m.group(2) or 1)
  return b"\n".join(out)


def names(rev_args: list[str], *extra: str) -> list[str]:
  raw = git(*GIT_DIFF, *rev_args, "--name-only", "-z", *extra).split(b"\0")
  return [p.decode("utf-8", errors="replace") for p in raw if p]


def scan_diff(fp, rev_args: list[str], label: str = "") -> list[str]:
  # Содержимое смотрим как текст в любом случае: --text, без textconv и внешнего diff,
  # без --diff-filter, чтобы не пропустить смену типа файла (symlink).
  hits = []
  for path in names(rev_args, "--diff-filter=ACMRT"):
    if os.path.basename(path).casefold().startswith(BLOCKED_PREFIXES):
      hits.append(f"blocked: {label}{path}: filename #1")
  # git diff по явному пути, чтобы путь в отчёте не зависел от кавычек и префиксов.
  for path in names(rev_args):
    diff = git("-c", "core.quotepath=false", *GIT_DIFF, "--text", "--no-textconv",
               "--no-ext-diff", "-U0", *rev_args, "--", path)
    for kind, n in find_hits(fp, added_bytes(diff)):
      hits.append(f"blocked: {label}{path}: {kind} #{n}")
  return hits


def scan_message(fp, raw: bytes, label: str) -> list[str]:
  return [f"blocked: {label}: {kind} #{n}" for kind, n in find_hits(fp, raw)]


def empty_tree() -> str:
  res = subprocess.run(["git", "hash-object", "-t", "tree", "--stdin"], input=b"", capture_output=True)
  if res.returncode != 0:
    raise ScanError("git hash-object failed")
  return res.stdout.decode().strip()


def scan_commit(fp, sha: str) -> list[str]:
  # Каждый коммит диапазона целиком: итоговый дифф диапазона скрыл бы
  # персону, добавленную одним коммитом и удалённую следующим.
  parents = git("rev-list", "--parents", "-n1", sha).decode().split()[1:]
  base = parents[0] if parents else empty_tree()
  label = sha[:12] + ":"
  hits = scan_diff(fp, [base, sha], label)
  return hits + scan_message(fp, git("log", "-n1", "--format=%B", sha), label + "commit-message")


def has_object(sha: str) -> bool:
  # Код 1 у cat-file -e значит "объекта нет"; любой другой сбой не маскируем.
  try:
    code = subprocess.run(["git", "cat-file", "-e", sha], capture_output=True).returncode
  except OSError as e:
    raise ScanError(f"cannot run git: {e.strerror}")
  if code not in (0, 1):
    raise ScanError(f"git command failed with code {code}")
  return code == 0


def scan_tags(fp, sha: str, label: str) -> tuple[str, list[str]]:
  # Тела аннотированных тегов, включая вложенные, до первого не-тега.
  hits = []
  while git("cat-file", "-t", sha).strip() == b"tag":
    body = git("cat-file", "-p", sha)
    hits += scan_message(fp, body, label)
    first = body.split(b"\n", 1)[0].split()
    if len(first) != 2 or first[0] != b"object":
      raise ScanError("malformed tag object")
    sha = first[1].decode()
  return sha, hits


def scan_pre_push(fp, stdin) -> list[str]:
  hits = []
  for line in stdin.read().splitlines():
    parts = line.split()
    if len(parts) != 4:
      continue
    _local_ref, local, remote_ref, remote = parts
    if set(local) == {"0"}:
      continue  # удаление ветки: отправлять нечего
    hits += scan_message(fp, remote_ref.encode(), "remote-ref")
    target, tag_hits = scan_tags(fp, local, remote_ref)
    hits += tag_hits
    # Неизвестный локально remote sha (force-push из устаревшего клона): как новая ветка.
    if set(remote) == {"0"} or not has_object(remote):
      rev = [target, "--not", "--remotes"]
    else:
      rev = [f"{remote}..{target}"]
    for sha in git("rev-list", "--reverse", *rev).decode().split():
      hits += scan_commit(fp, sha)
  return hits


def main() -> int:
  ap = argparse.ArgumentParser(description=__doc__)
  ap.add_argument("--root", default=os.environ.get("PERSONA_GUARD_ROOT") or str(Path.home()))
  ap.add_argument("--pre-push", action="store_true")
  args = ap.parse_args()

  fp = load_fingerprints(Path(args.root))
  if args.pre_push:
    hits = scan_pre_push(fp, sys.stdin)
  else:
    hits = scan_diff(fp, ["--cached"])

  if hits:
    print("\n".join(dict.fromkeys(hits)))
    return 1
  return 0


if __name__ == "__main__":
  try:
    sys.exit(main())
  except ScanError as e:
    print(f"persona-guard: {e}", file=sys.stderr)
    sys.exit(2)
  except Exception as e:
    # Любой сбой не должен превращаться в пропуск коммита.
    print(f"persona-guard: internal error: {type(e).__name__}", file=sys.stderr)
    sys.exit(2)
