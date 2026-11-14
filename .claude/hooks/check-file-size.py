#!/usr/bin/env python3
"""PreToolUse guardrail for the Read tool: blocks reads that return too many lines.

The size of a call is the number of lines that Read returns for it:
min(limit or 2000, lines from offset to the end of the file), the same formula with
or without offset and limit. 2000 is what Read returns when there is no limit, so a
whole-file read of a longer file is a 2000-line read. A limit or offset that is not a
positive integer (a bool too) counts as not set. The pages parameter is ignored: .pdf
files are skipped by extension, any other file is sized as usual. When the size is
above the threshold (get_threshold(): env CLAUDE_READ_LINE_LIMIT, default 350), the
hook denies the call. The variable counts only as ASCII digits with optional whitespace
around them (the same rule as the JS port); anything else ("+100", "1_000", "-5", "abc")
gives 350. "0" is valid: the threshold is 0, so every Read of a non-empty file is denied
("не больше 0 строк за вызов"). This is intended; the same holds for the JS port.

The denial tells the agent to read the file in chunks of at most <threshold> lines
with offset/limit (the main agent can also delegate the reading to Explore). For
.gd, .cs, .ts, .tsx, .js, .jsx, .mjs, .cjs, .py and .md files it adds an outline:
up to 60 lines "L<line>: <symbol>" that help the agent to choose a chunk. The hook
runs before the permission rules and before the permission prompt, so the outline must
not quote a file that a Read rule closes, and must not show the structure of a file
before the user has opened it to the session. Two conditions give no outline (the
denial itself does not change, except for the hint below):
- The path is closed to Read by the global ~/.claude/settings.json (.env, .env.*,
  secrets/). MIRRORED_READ_DENY_RULES is a hand-made copy of those deny rules, and a
  test fails when the global file has a Read rule that is not in the copy. The copy
  mirrors the global file only: the hook does not read project settings or managed
  policy, so a path that only they close still gets an outline.
- The file is outside the working directory of the session. The working directory is
  the `cwd` of the payload, or the directory of the process when the payload has none.
  The file and the directory are compared by real path (links resolved) and by path
  components, so /a/bc is not inside /a/b. On macOS (APFS is case-insensitive) the
  components are compared without regard to letter case. On a case-sensitive APFS volume,
  the hook treats files in a directory that differs from the working directory only in letter
  case as inside. This is a known limitation. Protected paths are checked before this
  comparison and are unaffected. If the working directory cannot be found, there is no
  outline. Trade-off: a file that the session may read outside the working directory
  (additionalDirectories, --add-dir, an allow rule) gets no outline either.
Only this second case adds one hint line to the denial (OUTSIDE_HINT), so the agent knows
that the outline is missing on purpose. A closed path gets no hint (it would show that the
path is closed), and neither does a file whose outline failed or whose extension has none.

The hook never grants access. Every other path exits 0 with no output: the hook
decides nothing and Read goes through the normal permission rules.

Mirrors the "check-file-size" hook described in Spotify's Portal writeup, minus
the routing to an external cheap model — this only enforces the block, it doesn't
provide a replacement reader.

Fails open on any error (missing file, unreadable, unexpected exception): this is
a cost/context-hygiene guardrail, not a security boundary, so a bug here should
never be able to break normal Read usage.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LINE_LIMIT = 350
READ_DEFAULT_LINES = 2000  # сколько строк Read отдаёт без limit
OUTLINE_MAX_ENTRIES = 60
OUTLINE_MAX_WIDTH = 120
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

# Символы для оглавления отказа: шаблон по расширению, совпадение от начала строки с отступом.
# Шаблоны идут за строкой линейно: строка в мегабайты не должна вешать хук.
CS_MODIFIERS = (
    r"(?:(?:public|private|protected|internal|static|sealed|abstract|partial|readonly"
    r"|unsafe|new|file)\s+)*"
)
JS_SYMBOL = re.compile(
    r"\s*(?:export[\s{*]|(?:async\s+)?function[\s*]|(?:abstract\s+)?class\s"
    r"|(?:describe|it|test)(?:\.(?:only|skip))?\()"
)
SYMBOL_PATTERNS = {
    ".gd": re.compile(r"\s*(?:static\s+func|func|class_name|class|signal)\s"),
    # C#: тип или метод (модификатор доступа, тип, имя, «(»); метод со «;» в конце строки не берём
    ".cs": re.compile(
        r"\s*(?:" + CS_MODIFIERS + r"(?:class|struct|interface|enum|record)\s+\w"
        r"|(?:public|private|protected|internal)\s(?!.*;\s*$)[^=;(){}]*?[\w>]\s*\()"
    ),
    ".py": re.compile(r"\s*(?:async\s+def|def|class)\s"),
    ".md": re.compile(r" {0,3}#{1,6}(?:\s|$)"),
    **dict.fromkeys((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"), JS_SYMBOL),
}
MD_FENCE = re.compile(r"\s*(`{3,}|~{3,})(.*)")
OUTSIDE_HINT = "Оглавления нет: файл вне рабочей папки сессии."
# Порог: как JS /^\s*\d+\s*$/ — только ASCII-цифры, пробелы по краям из набора \s в JS
JS_SPACE = "\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
THRESHOLD_FORMAT = re.compile(rf"[{JS_SPACE}]*([0-9]+)[{JS_SPACE}]*")

# Правила Read из permissions.deny глобального ~/.claude/settings.json, которые повторяет
# is_protected. Список зеркалит только глобальный файл: проектные settings и managed policy
# хук не читает. Тест в test_dsh_adapter.py падает, если в settings.json есть Read-правило
# вне списка.
MIRRORED_READ_DENY_RULES = ("Read(.env)", "Read(.env.*)", "Read(**/secrets/**)")


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


def count_lines(path):
    count = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            count += chunk.count(b"\n")
    return count


def get_threshold():
    match = THRESHOLD_FORMAT.fullmatch(os.environ.get("CLAUDE_READ_LINE_LIMIT", ""))
    return int(match.group(1)) if match else DEFAULT_LINE_LIMIT


def positive_int(value: object) -> int | None:
    """Значение, если это целое больше нуля (bool не считается), иначе None: параметр не задан."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def read_size(tool_input: dict[str, object], lines: int) -> int:
    """Сколько строк вернёт Read для файла из `lines` строк."""
    limit = positive_int(tool_input.get("limit"))
    offset = positive_int(tool_input.get("offset"))
    remaining = max(lines - (offset or 1) + 1, 0)
    return min(limit or READ_DEFAULT_LINES, remaining)


def outside_fences(numbered: Iterable[tuple[int, str]]) -> Iterator[tuple[int, str]]:
    """Строки Markdown вне блоков кода.

    Блок закрывает забор того же символа, не короче открывающего и без текста после него:
    ``` внутри ```` не сбивает счёт.
    """
    fence = ""
    for number, line in numbered:
        marker = MD_FENCE.match(line)
        if not fence:
            if marker:
                fence = marker.group(1)
            else:
                yield number, line
        elif (marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence)
              and not marker.group(2).strip()):
            fence = ""


def is_protected(path: Path) -> bool:
    """Путь под правилами MIRRORED_READ_DENY_RULES.

    Имена сравниваются без учёта регистра: на APFS `.ENV.py` и `.env.py` — один файл.
    """
    name = path.name.lower()
    return (name == ".env" or name.startswith(".env.")
            or any(part.lower() == "secrets" for part in path.parts))


def is_inside(resolved: Path, base: Path) -> bool:
    """Лежит ли resolved в base: по компонентам пути, на macOS без учёта регистра (APFS)."""
    parts, base_parts = resolved.parts, base.parts
    if sys.platform == "darwin":
        parts = tuple(p.lower() for p in parts)
        base_parts = tuple(p.lower() for p in base_parts)
    return parts[: len(base_parts)] == base_parts


def build_outline(path: Path, cwd: str | None) -> str:
    """Оглавление файла для текста отказа: строки «L<номер>: символ», не больше OUTLINE_MAX_ENTRIES.

    Пустая строка, если у расширения нет шаблона, символов нет, путь закрыт правилами Read,
    файл вне рабочего каталога сессии или файл не прочитался: отказ не должен зависеть от
    оглавления. Хук идёт раньше проверки разрешений, поэтому из закрытого файла оглавление не
    должно выдать ни строки, а из файла вне рабочего каталога — структуру до запроса разрешения.
    Рабочий каталог — `cwd` из payload, без него каталог процесса. Файл и каталог сравниваются
    по реальным путям и по компонентам пути: /a/bc не внутри /a/b. Каталог не определён
    (cwd не путь, каталог процесса удалён) — оглавления нет.
    """
    suffix = path.suffix.lower()
    pattern = SYMBOL_PATTERNS.get(suffix)
    if pattern is None:
        return ""
    shown: list[str] = []
    total = 0
    try:
        resolved = path.resolve()
        if is_protected(path) or is_protected(resolved):
            return ""
        # additionalDirectories / --add-dir тоже вне cwd: оглавления у них нет, компромисс принят
        if not is_inside(resolved, Path(cwd or os.getcwd()).resolve()):
            return ""
        # newline="\n": строки делятся только по \n, как их нумерует Read и считает count_lines;
        # utf-8-sig: BOM в начале файла не прячет символ первой строки
        with resolved.open(encoding="utf-8-sig", errors="replace", newline="\n") as f:
            numbered: Iterable[tuple[int, str]] = enumerate(f, 1)
            if suffix == ".md":
                numbered = outside_fences(numbered)
            for number, line in numbered:
                if pattern.match(line):
                    total += 1
                    if total <= OUTLINE_MAX_ENTRIES:
                        shown.append(f"L{number}: {line.strip()[:OUTLINE_MAX_WIDTH]}")
    except Exception:
        return ""  # оглавление — приложение к отказу, его сбой отказ не отменяет
    if total > OUTLINE_MAX_ENTRIES:
        shown.append(f"… ещё {total - OUTLINE_MAX_ENTRIES} символов не показано")
    return "\n".join(shown)


def outside_hint(path: Path, cwd: str | None) -> str:
    """OUTSIDE_HINT, если у файла нет оглавления именно потому, что он вне рабочего каталога.

    Пусто для расширения без шаблона, закрытого пути и любого сбоя (в том числе каталог не определён).
    """
    if path.suffix.lower() not in SYMBOL_PATTERNS:
        return ""
    try:
        resolved = path.resolve()
        if is_protected(path) or is_protected(resolved):
            return ""
        return "" if is_inside(resolved, Path(cwd or os.getcwd()).resolve()) else OUTSIDE_HINT
    except Exception:
        return ""


def main() -> None:
    data = json.load(sys.stdin)
    if data.get("tool_name") != "Read":
        return

    tool_input = data.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path")
    if not file_path:
        return

    path = Path(file_path)
    if not path.is_file() or path.suffix.lower() in SKIP_EXTENSIONS:
        return

    lines = count_lines(path)
    threshold = get_threshold()

    if read_size(tool_input, lines) <= threshold:
        return

    # У саб-агентов нет инструмента Agent: первым идёт совет, который выполним для любого агента.
    reason = (
        f"Файл {file_path} — {lines} строк, порог {threshold}. "
        f"Читай частями: Read с offset и limit, не больше {threshold} строк за вызов. "
        "Главный агент может делегировать чтение Explore."
    )
    log_event(file_path, lines, threshold, "deny", reason)
    cwd = data.get("cwd")
    outline = build_outline(path, cwd)
    if outline:
        deny(f"{reason}\n\nСтруктура файла:\n{outline}")
    hint = outside_hint(path, cwd)
    deny(f"{reason}\n\n{hint}" if hint else reason)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # fail-open: только запись в лог и ни строки в stdout, Read идёт обычным путём разрешений
        log_event(None, None, None, "allow-on-error", repr(e))
