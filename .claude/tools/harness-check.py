"""Consistency check for the three harness configs under ROOT (default: $HOME)."""
import json, re, sys, tomllib
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home()
C, X, O = ROOT / ".claude", ROOT / ".codex", ROOT / ".config/opencode"
OLD = ["code-reviewer", "sql-data-reviewer", "sql-reviewer", "worker-t1", "worker-t2", "worker-t3",
       "web-researcher-fast", "docs-researcher-deep", "issue-tracker.md", "setup-matt-pocock"]
SHARED = ["code-review", "sql", "sql-review", "trino-execution", "debugging", "appsec-review"]
CLAUDE_ONLY_AGENTS = {"magi-melchior", "magi-balthasar", "magi-casper"}  # Claude-only: rely on Agent Teams
problems = []

def skills(d):
    return {p.parent.name for p in d.glob("*/SKILL.md")}

agents = {
    "claude": {p.stem for p in (C / "agents").glob("*.md")},
    "opencode": {p.stem for p in (O / "agent").glob("*.md")},
    "codex": {tomllib.loads(p.read_text())["name"] for p in (X / "agents").glob("*.toml")},
}
for p in (X / "agents").glob("*.toml"):
    if tomllib.loads(p.read_text())["name"] != p.stem:
        problems.append(f"codex agent file/name mismatch: {p.name}")
base = agents["claude"] - CLAUDE_ONLY_AGENTS
for h, s in agents.items():
    if h == "claude":
        continue
    if s != base:
        problems.append(f"agent set differs: {h} has {sorted(s ^ base)} vs claude")

claude_sk = skills(C / "skills")
codex_sk = skills(X / "skills")
jsonc = (O / "opencode.jsonc").read_text()
allow = set(re.findall(r'^\s*"([a-z0-9-]+)": "allow"', jsonc.split('"skill": {', 1)[1].split("}", 1)[0], re.M))
opencode_sk = (claude_sk & allow) | skills(O / "skills")
missing_allow = allow - claude_sk - skills(O / "skills")
if missing_allow:
    problems.append(f"opencode allowlist names skills that do not exist: {sorted(missing_allow)}")

for s in SHARED:
    a, b = C / "skills" / s / "SKILL.md", X / "skills" / s / "SKILL.md"
    if not a.exists() or not b.exists() or a.read_text() != b.read_text():
        problems.append(f"shared skill not identical in claude/codex: {s}")

# Skills that exist in Codex/OpenCode must exist in Claude (plugin exception noted).
EXCEPT = {"coding-task-planner", "task-management"}
for s in (codex_sk | skills(O / "skills")) - claude_sk - EXCEPT:
    problems.append(f"skill missing in claude: {s}")

vocab = set().union(*agents.values()) | claude_sk | codex_sk | set(OLD)
BUILTIN = {"claude": {"Explore", "general-purpose", "Plan", "security-review"}, "codex": set(), "opencode": {"plan"}}
PLUGIN = {"claude": {"coding-task-planner", "task-management"}}
scopes = {
    "claude": ([C / "agents", C / "skills/task-orchestration", C / "skills/code-tier-assessment", C / "docs", C / "CLAUDE.md"]
               + [C / "skills" / s for s in SHARED], agents["claude"] | claude_sk),
    "codex": ([X / "agents", X / "skills", X / "AGENTS.md"], agents["codex"] | codex_sk),
    "opencode": ([O / "agent", O / "skills"], agents["opencode"] | opencode_sk),
}
tok = re.compile(r"`([A-Za-z][A-Za-z0-9._-]+)`")
for h, (paths, have) in scopes.items():
    files = []
    for p in paths:
        files += [p] if p.is_file() else [f for f in p.rglob("*") if f.suffix in (".md", ".toml") and f.is_file()]
    for f in files:
        text = f.read_text(errors="replace")
        for o in OLD:
            if re.search(r"(?<![\w-])" + re.escape(o) + r"(?![\w-])", text):
                problems.append(f"{h}: old name `{o}` in {f.relative_to(ROOT)}")
        for t in set(tok.findall(text)):
            if t in vocab and t not in have and t not in BUILTIN[h] and t not in PLUGIN.get(h, set()) and t not in OLD:
                problems.append(f"{h}: `{t}` referenced in {f.relative_to(ROOT)} but not available")

# Персона: импорт в OpenCode, комментарии jsonc не считаются.
def strip_jsonc_comments(src):
    # Построчный сканер с учётом строк: "//" внутри "..." (например http://) не комментарий.
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c == '"':
            j = i + 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1])
            i = j + 1
        elif src.startswith("//", i):
            while i < n and src[i] != "\n":
                i += 1
        elif src.startswith("/*", i):
            end = src.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)

if str(C / "persona.md") not in strip_jsonc_comments(jsonc):
    problems.append("opencode instructions do not include persona.md")

print("agents:", sorted(base))
print("problems:", len(problems))
for p in problems:
    print(" -", p)
sys.exit(1 if problems else 0)
