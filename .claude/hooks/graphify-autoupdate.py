#!/usr/bin/env python3
"""PostToolUse: keep a repo's graphify code graph fresh after Claude changes files.

Opt-in per repository: acts only when the git root of the touched file (or of
the Bash cwd) already has a `graphify-out/` directory. Every worktree has its
own git root and its own graph, so each one is rebuilt independently.

  - Edit/Write/MultiEdit/NotebookEdit: rebuild when the file has a code
    extension graphify parses.
  - Bash: the tool input does not say which files changed (sed, heredocs,
    git checkout), so rebuild when any code file is newer than the stamp of
    the last rebuild.

The hook itself returns in milliseconds: the rebuild (`graphify update .`,
AST only, no LLM) runs in a detached child. One rebuild per root at a time;
changes that arrive mid-rebuild set a dirty flag and trigger one more pass.

Not covered: edits made outside Claude Code (Godot editor, Codex, manual).
Fails open on any error: a broken graph update must never break a tool call.
Log: <root>/graphify-out/autoupdate.log (graphify-out is gitignored).
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Must match what graphify extracts (graphify.detect.CODE_EXTENSIONS).
CODE_EXTENSIONS = {
    ".py", ".ts", ".js", ".jsx", ".tsx", ".mjs", ".ejs", ".go", ".rs", ".java",
    ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".rb", ".swift", ".kt", ".kts",
    ".cs", ".scala", ".php", ".lua", ".toc", ".zig", ".ps1", ".ex", ".exs",
    ".m", ".mm", ".jl", ".vue", ".svelte", ".dart", ".v", ".sv",
    ".gd", ".tscn", ".tres",
}
PRUNE_DIRS = {"graphify-out", "node_modules", "__pycache__"}
OUT = "graphify-out"
STAMP = ".autoupdate-stamp"
DIRTY = ".autoupdate-dirty"
LOCK = ".autoupdate.lock"
LOG = "autoupdate.log"
LOG_MAX_BYTES = 200_000
GIT_WAIT_SECONDS = 30
UPDATE_TIMEOUT = 180


def git_root(start: Path) -> Path | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return Path(out.stdout.strip())


def ignored_dirs(root: Path) -> set[str]:
    """Top-level directory names from .graphifyignore (`addons/` style lines)."""
    names = set()
    try:
        for line in (root / ".graphifyignore").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                names.add(line.strip("/"))
    except OSError:
        pass
    return names


def code_changed_since(root: Path, since: float) -> bool:
    skip = PRUNE_DIRS | ignored_dirs(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in skip]
        for name in filenames:
            if Path(name).suffix.lower() not in CODE_EXTENSIONS:
                continue
            try:
                if os.stat(os.path.join(dirpath, name)).st_mtime > since:
                    return True
            except OSError:
                continue
    return False


def last_rebuild_time(out: Path) -> float:
    for name in (STAMP, "graph.json"):
        try:
            return (out / name).stat().st_mtime
        except OSError:
            continue
    return 0.0


def graphify_bin() -> str | None:
    found = shutil.which("graphify")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "graphify"
    return str(fallback) if fallback.exists() else None


def log(out: Path, message: str) -> None:
    path = out / LOG
    try:
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            path.replace(out / (LOG + ".1"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")
    except OSError:
        pass


def wait_for_git(root: Path) -> None:
    """Do not rebuild in the middle of a checkout/merge: wait for index.lock to go."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-path", "index.lock"],
            capture_output=True, text=True, timeout=5,
        )
        lock = Path(out.stdout.strip())
        if not lock.is_absolute():
            lock = root / lock
    except Exception:
        return
    deadline = time.monotonic() + GIT_WAIT_SECONDS
    while lock.exists() and time.monotonic() < deadline:
        time.sleep(0.5)


def run_updates(root: Path) -> None:
    out = root / OUT
    dirty = out / DIRTY
    dirty.touch()
    binary = graphify_bin()
    if binary is None:
        log(out, "graphify not found on PATH or in ~/.local/bin; skipped")
        return
    while True:
        lock_fh = (out / LOCK).open("w")
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock_fh.close()
            return  # another rebuild is running and will see the dirty flag
        try:
            while dirty.exists():
                dirty.unlink(missing_ok=True)
                wait_for_git(root)
                # Stamp before the run: files edited during it stay "newer".
                (out / STAMP).touch()
                started = time.monotonic()
                proc = subprocess.run(
                    [binary, "update", "."], cwd=root,
                    capture_output=True, text=True, timeout=UPDATE_TIMEOUT,
                )
                tail = " | ".join((proc.stdout + proc.stderr).strip().splitlines()[-3:])
                log(out, f"rc={proc.returncode} {time.monotonic() - started:.1f}s {tail}")
        except Exception as exc:
            log(out, f"error: {exc!r}")
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
            lock_fh.close()
        # A change may have landed between the last check and the unlock.
        if not dirty.exists():
            return


def spawn(root: Path) -> None:
    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--run", str(root)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, close_fds=True,
    )


def hook() -> None:
    data = json.load(sys.stdin)
    tool = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    if tool == "Bash":
        start = Path(data.get("cwd") or os.getcwd())
        file_path = None
    else:
        raw = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not raw:
            return
        file_path = Path(raw)
        if file_path.suffix.lower() not in CODE_EXTENSIONS:
            return
        start = file_path.parent
    if not start.is_dir():
        return
    root = git_root(start)
    if root is None or not (root / OUT).is_dir():
        return
    if file_path is None and not code_changed_since(root, last_rebuild_time(root / OUT)):
        return
    spawn(root)


def main() -> None:
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--run":
            run_updates(Path(sys.argv[2]))
        else:
            hook()
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
