#!/usr/bin/env python3
"""PreToolUse guardrail for Edit/Write: blocks weakening a linter or type-checker config.

The completion gate says "no validation was disabled or weakened merely to pass".
Nothing enforced it. When `ruff` complains, editing `ruff.toml` is the cheapest
way to make the complaint stop, and the run still reports green. This turns that
line from a promise into a gate.

Two kinds of file, handled differently:

  - Dedicated configs (`ruff.toml`, `.flake8`, `.pre-commit-config.yaml`, ...):
    the whole file is tuning, so any modification is denied. Creating one where
    none exists is allowed — there is nothing to weaken yet.
  - Mixed files (`pyproject.toml`, `setup.cfg`, `tox.ini`): they hold
    dependencies and project metadata next to the tool settings. Blocking them
    outright would forbid legitimate work, so the edit is applied in memory and
    only the `[tool.ruff]`-class sections are compared before and after. A
    dependency bump passes; a widened `ignore` list does not.

Ported in spirit from ECC's `pre:config-protection`, with the mixed-file
comparison added: upstream skips `pyproject.toml` entirely, which in a Python
repo is where the ruff and mypy settings usually live.

Not a security boundary. Fails open on any error, and an agent that genuinely
needs a config change can ask the user or set CLAUDE_CONFIG_PROTECTION=0.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path.home() / ".claude" / "logs" / "config-protection.jsonl"

# Every entry lowercase; matching lowercases the basename too. On macOS APFS a
# write to `.ESLINTRC.JS` lands on the same inode as `.eslintrc.js`, so a
# case-sensitive lookup would let a case-variant slip straight past the guard.
DEDICATED = {
    # Python
    "ruff.toml", ".ruff.toml",
    "mypy.ini", ".mypy.ini",
    ".flake8",
    "pytest.ini",
    ".pylintrc", "pylintrc",
    ".isort.cfg",
    ".bandit", "bandit.yaml",
    ".pre-commit-config.yaml", ".pre-commit-config.yml",
    # SQL
    ".sqlfluff",
    # Shell and markup
    ".shellcheckrc",
    ".yamllint", ".yamllint.yaml", ".yamllint.yml",
    ".markdownlint.json", ".markdownlint.yaml", ".markdownlintrc",
    ".editorconfig",
    # JS/TS, in case a project carries one
    ".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json",
    ".eslintrc.yml", ".eslintrc.yaml",
    "eslint.config.js", "eslint.config.mjs", "eslint.config.cjs", "eslint.config.ts",
    "biome.json", "biome.jsonc",
    ".prettierrc", ".prettierrc.json", ".prettierrc.js", ".prettierrc.yml",
    ".stylelintrc", ".stylelintrc.json",
}

# Files where tool settings sit next to things an agent may legitimately change.
MIXED = {"pyproject.toml", "setup.cfg", "tox.ini"}

SECTION_HEADER = re.compile(r"^[ \t]*\[([^\]\n]+)\][ \t]*$", re.M)

# `[tool.ruff.lint]`, `[tool:pytest]`, `[flake8]`, `[coverage:report]` all match.
# `[project]`, `[build-system]` and `[tool.poetry.dependencies]` do not, so
# dependency and metadata edits pass through untouched.
TOOL_SECTION = re.compile(
    r"^(?:tool[.:])?"
    r"(ruff|mypy|flake8|pytest|coverage|black|isort|pylint|pycodestyle|pydocstyle|bandit|sqlfluff)\b",
    re.I,
)


def log_event(file_path, decision, reason, sections=None):
    """Path and decision only. The payload may hold anything, so it never lands here."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a") as handle:
            handle.write(json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "file_path": file_path,
                "decision": decision,
                "sections": sections,
                "reason": reason,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass  # logging must never be the reason an edit fails


def respond(decision, reason=None):
    payload = {"hookEventName": "PreToolUse", "permissionDecision": decision}
    if reason:
        payload["permissionDecisionReason"] = reason
    print(json.dumps({"hookSpecificOutput": payload}))
    sys.exit(0)


def allow():
    respond("allow")


def deny(file_path, reason, sections=None):
    log_event(file_path, "deny", reason, sections)
    respond("deny", reason)


def exists(path):
    """True unless the path is genuinely absent.

    `Path.exists()` reports False on EACCES and EPERM, which would read as
    "no config here yet" and wave the write through. Only ENOENT counts as
    absent; every other error leaves the guard in place.
    """
    try:
        os.lstat(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True


def tool_sections(text):
    """Map every linter/type-checker section in an INI or TOML file to its body."""
    found = {}
    headers = list(SECTION_HEADER.finditer(text))
    for index, header in enumerate(headers):
        name = header.group(1).strip()
        if not TOOL_SECTION.match(name):
            continue
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        found[name] = text[header.end():end].strip()
    return found


def apply_edit(current, tool_input):
    """The file as this call would leave it, or None when that cannot be determined."""
    if "content" in tool_input:  # Write replaces the whole file
        content = tool_input.get("content")
        return content if isinstance(content, str) else None

    old = tool_input.get("old_string")
    new = tool_input.get("new_string")
    if not isinstance(old, str) or not isinstance(new, str) or old not in current:
        return None  # the Edit will fail on its own; not this hook's business
    return current.replace(old, new) if tool_input.get("replace_all") else current.replace(old, new, 1)


def main():
    if os.environ.get("CLAUDE_CONFIG_PROTECTION") == "0":
        allow()
        return

    data = json.load(sys.stdin)
    if data.get("tool_name") not in ("Edit", "Write"):
        allow()
        return

    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path")
    if not file_path:
        allow()
        return

    basename = os.path.basename(file_path).lower()
    if basename not in DEDICATED and basename not in MIXED:
        allow()
        return

    if not exists(file_path):
        allow()  # writing a config into a project that has none is a bootstrap, not a weakening
        return

    if basename in DEDICATED:
        deny(file_path, (
            f"Правка {basename} заблокирована. Этот файл целиком настраивает проверки, "
            "и менять его, когда проверка не проходит, — значит ослаблять проверку, "
            "а не чинить код. Почини код. Если менять конфиг и есть сама задача — "
            "спроси пользователя; временно снять гард можно через CLAUDE_CONFIG_PROTECTION=0."
        ))
        return

    try:
        current = Path(file_path).read_text(errors="replace")
    except OSError:
        allow()  # cannot read it, so cannot judge it
        return

    updated = apply_edit(current, tool_input)
    if updated is None:
        allow()
        return

    before, after = tool_sections(current), tool_sections(updated)
    changed = sorted(
        name for name in set(before) | set(after)
        if before.get(name) != after.get(name)
    )
    if not changed:
        allow()  # dependencies, metadata, scripts — none of this hook's concern
        return

    deny(file_path, (
        f"Правка {basename} меняет настройки проверок: {', '.join(changed)}. "
        "Ослаблять их, чтобы проверка прошла, нельзя — почини код. "
        "Остальные секции этого файла (зависимости, метаданные) гард не трогает. "
        "Если правка конфига и есть задача — спроси пользователя; "
        "временно снять гард можно через CLAUDE_CONFIG_PROTECTION=0."
    ), changed)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        log_event(None, "allow-on-error", repr(error))
        allow()
