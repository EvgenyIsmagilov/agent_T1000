"""Shared brief-field regexes: what the orchestrator names in a delegation brief.

Extracted from log-subagent.py so require-orchestration.py can reuse the exact
`slice_id` pattern without duplicating it or importing a hyphenated filename
(which is not a valid Python module name).
"""
import re

# Fields the orchestrator names in the delegation brief. Matched on their
# own line, optionally as a list item and optionally backticked, so prose
# that merely mentions a slice cannot be mistaken for a declaration.
BRIEF_FIELDS = {
    "slice_id": r"[A-Za-z0-9._-]{1,64}",
    "tier": r"T[123]",
    "previous_failure_class": r"executor|spec|env|slicing",
}
BRIEF_RE = {
    name: re.compile(
        r"^[ \t]*[-*]?[ \t]*`?" + name + r"`?[ \t]*[:=][ \t]*`?(" + pattern + r")`?",
        re.MULTILINE | re.IGNORECASE,
    )
    for name, pattern in BRIEF_FIELDS.items()
}

# A value needs at least one letter or digit: punctuation alone does not count.
# The JS twin in claude-hooks.js must use the same class text.
_VALUE = r"[ \t]*[^\n]*?[A-Za-z0-9А-Яа-яЁё]"

# Checklist lines required in a code-writer-t2/t3 brief. Same line-anchored
# form as BRIEF_RE, but any non-empty value on the same line counts: the hook
# checks presence, not truthfulness. Kept out of BRIEF_FIELDS so the
# log-subagent.py output does not change.
CHECKLIST_FIELDS = ("invariant_paths", "data_sample", "spec_check", "divisible")
CHECKLIST_RE = {
    name: re.compile(
        r"^[ \t]*[-*]?[ \t]*`?" + name + r"`?[ \t]*[:=]" + _VALUE,
        re.MULTILINE | re.IGNORECASE,
    )
    for name in CHECKLIST_FIELDS
}
# `divisible` строже: значение начинается с целого слова yes/no (не `yesterday`,
# не `none`), затем на той же строке причина с буквой или цифрой.
# JS-близнец в claude-hooks.js должен использовать тот же текст класса.
CHECKLIST_RE["divisible"] = re.compile(
    r"^[ \t]*[-*]?[ \t]*`?divisible`?[ \t]*[:=][ \t]*`?(?:yes|no)(?![A-Za-z0-9_А-Яа-яЁё|/])`?"
    r"[^\n]*?[A-Za-z0-9А-Яа-яЁё]",
    re.MULTILINE | re.IGNORECASE,
)

# Brief metrics logged by log-subagent.py (schema 7). Kept out of BRIEF_FIELDS
# so BRIEF_RE users (gates, dsh_adapter) are unaffected. `divisible` takes a
# whole word yes/no, so placeholders like `yes|no` give no match. The field name
# has balanced backticks or none, so an inline-code example (`volume: 9 ...`)
# is skipped. Fenced ``` blocks are removed by strip_fences() before searching
# (one linear pass; a regex lookahead per line was O(n^2)).
# A diff line (`-divisible: yes`) is not told apart from a list item: not handled.
_FENCE_RE = re.compile(
    r"^ {0,3}(?:(`{3,})[^`\n]*(?:\n.*?(?:^ {0,3}\1`*[ \t]*$|\Z)|\Z)"
    r"|(~{3,})[^\n]*(?:\n.*?(?:^ {0,3}\2~*[ \t]*$|\Z)|\Z))",
    re.MULTILINE | re.DOTALL,
)


def strip_fences(text: str) -> str:
    """Remove CommonMark fenced blocks. An unclosed fence hides the rest of the text."""
    return _FENCE_RE.sub("", text)


_NUM = r"\d{1,3}(?:[\xa0 ,_]\d{3})+|\d+"
METRIC_RE = {
    "divisible": re.compile(
        r"^[ \t]*[-*]?[ \t]*(?:`divisible`|divisible)[ \t]*[:=][ \t]*`?(yes|no)(?![A-Za-z0-9_А-Яа-яЁё|/])",
        re.MULTILINE | re.IGNORECASE,
    ),
    "volume": re.compile(
        r"^[ \t]*[-*]?[ \t]*(?:`volume`|volume)[ \t]*[:=][ \t]*`?(" + _NUM + r")[ \t]*files?\b[ \t,;]*(" + _NUM
        + r")[ \t]*lines?\b",
        re.MULTILINE | re.IGNORECASE,
    ),
}
