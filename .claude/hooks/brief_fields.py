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
CHECKLIST_FIELDS = ("invariant_paths", "data_sample", "spec_check")
CHECKLIST_RE = {
    name: re.compile(
        r"^[ \t]*[-*]?[ \t]*`?" + name + r"`?[ \t]*[:=]" + _VALUE,
        re.MULTILINE | re.IGNORECASE,
    )
    for name in CHECKLIST_FIELDS
}
