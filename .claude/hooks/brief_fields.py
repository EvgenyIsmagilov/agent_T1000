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
