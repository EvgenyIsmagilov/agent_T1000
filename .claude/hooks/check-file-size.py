#!/usr/bin/env python3
"""PreToolUse guardrail for the Read tool: blocks whole-file reads of large files.

Forces large whole-file reads through a subagent (Explore/general-purpose)
instead of dumping the whole file into the main context. A targeted read
(offset/limit/pages already set) is left alone — see the TARGETED_PARAMS
check below. Mirrors the "check-file-size" hook described in Spotify's
Portal writeup, minus the routing to an external cheap model — this only
enforces the block, it doesn't provide a replacement reader.

Fails open on any error (missing file, unreadable, unexpected exception):
this is a cost/context-hygiene guardrail, not a security boundary, so a bug
here should never be able to break normal Read usage.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LINE_LIMIT = 350
LOG_PATH = Path.home() / ".claude" / "logs" / "file-read-guardrail.jsonl"

# Extensions where a raw newline count is meaningless (binary/opaque formats).
# Read tool handles images/PDFs specially anyway; don't second-guess it here.
SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
    ".pdf", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".mp3", ".mp4", ".mov", ".avi", ".wav",
    ".exe", ".dll", ".so", ".dylib", ".bin",
    ".woff", ".woff2", ".ttf", ".otf", ".class", ".jar", ".pyc",
}

# Read-tool params that make a call "targeted" rather than a blind full read.
TARGETED_PARAMS = ("offset", "limit", "pages")


def log_event(file_path, lines, threshold, decision, reason):
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a") as f:
            f.write(json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "file_path": file_path,
                "lines": lines,
                "threshold": threshold,
                "decision": decision,
                "reason": reason,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass  # logging must never be the reason a read fails


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def allow():
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }))
    sys.exit(0)


def count_lines(path):
    count = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            count += chunk.count(b"\n")
    return count


def get_threshold():
    try:
        return int(os.environ.get("CLAUDE_READ_LINE_LIMIT", DEFAULT_LINE_LIMIT))
    except ValueError:
        return DEFAULT_LINE_LIMIT


def main():
    data = json.load(sys.stdin)
    if data.get("tool_name") != "Read":
        allow()
        return

    tool_input = data.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path")
    if not file_path:
        allow()
        return

    if any(tool_input.get(p) is not None for p in TARGETED_PARAMS):
        allow()  # offset/limit/pages => deliberate partial read, let it through
        return

    path = Path(file_path)
    if not path.is_file() or path.suffix.lower() in SKIP_EXTENSIONS:
        allow()
        return

    lines = count_lines(path)
    threshold = get_threshold()

    if lines <= threshold:
        allow()
        return

    reason = (
        f"Файл {file_path} — {lines} строк, порог {threshold}. "
        "Не читай целиком: делегируй чтение/анализ саб-агенту (subagent_type=Explore "
        "или general-purpose) и забери у него выжимку вместо сырого содержимого."
    )
    log_event(file_path, lines, threshold, "deny", reason)
    deny(reason)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_event(None, None, None, "allow-on-error", repr(e))
        allow()
