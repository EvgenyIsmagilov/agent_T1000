"""Consistency check for the three harness configs under ROOT (default: $HOME)."""
import json, re, sys, tomllib
from pathlib import Path

# Абсолютный путь: иначе логический путь .claude/persona.md входит в любой путь с таким хвостом.
# Не использовать resolve(): тесты передают ROOT через символьные ссылки.
ROOT = Path(sys.argv[1]).absolute() if len(sys.argv) > 1 else Path.home()
C, X, O = ROOT / ".claude", ROOT / ".codex", ROOT / ".config/opencode"
OLD = ["code-reviewer", "sql-data-reviewer", "sql-reviewer", "worker-t1", "worker-t2", "worker-t3",
       "web-researcher-fast", "docs-researcher-deep", "issue-tracker.md", "setup-matt-pocock"]
SHARED = ["code-review", "sql", "sql-review", "trino-execution", "debugging", "appsec-review"]
CLAUDE_ONLY_AGENTS = {"magi-melchior", "magi-balthasar", "magi-casper", "pr-review-local"}  # Claude-only: rely on Agent Teams (magi-*), wrapper for the local PR review script
# Разделы code-writer: внутри Claude и внутри OpenCode отдельно они должны совпадать побайтно.
SHARED_SECTIONS = [
    (["Minimize before writing", "Constraints", "Final output contract", "Noise limits"],
     ["code-writer-t1", "code-writer-t2", "code-writer-t3"]),
    (["Scope and isolation"], ["code-writer-t1", "code-writer-t2"]),
    (["Bug fixes", "Turn budget"], ["code-writer-t2", "code-writer-t3"]),
]
STATUS_TOKEN = re.compile(r"`(DONE|PARTIAL|BLOCKED|PASS|FAIL|HOLDS|REFUTED|UNDETERMINED|INCONCLUSIVE)`")
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


# Комментарии jsonc удаляются до json.loads.
def strip_jsonc_comments(src: str) -> str:
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


jsonc = (O / "opencode.jsonc").read_text()
# Разбор jsonc один раз; при ошибке разбора allowlist пуст, а проверка instructions пропускается.
parsed = True
try:
    config = json.loads(strip_jsonc_comments(jsonc))
except json.JSONDecodeError:
    parsed = False
    problems.append("opencode.jsonc does not parse as JSON")
allow: set[str] = set()
if parsed:
    permission = config.get("permission") if isinstance(config, dict) else None
    skill_rules = permission.get("skill") if isinstance(permission, dict) else None
    # Без блока permission.skill список разрешений пуст; остальные проверки продолжаются.
    if isinstance(skill_rules, dict):
        allow = {name for name, rule in skill_rules.items() if rule == "allow" and re.fullmatch(r"[a-z0-9-]+", name)}
    else:
        problems.append("opencode.jsonc has no skill allowlist")
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

# Персона: jsonc хранит логический путь. Настоящий путь (resolve()) тоже годится, тестовый ROOT состоит из ссылок.
# Запись из списка instructions должна совпасть целиком: подстрока приняла бы persona.md.bak и /backup/<путь>.
persona = C / "persona.md"
accepted = (str(persona), str(persona.resolve()))
if parsed:
    instructions = config.get("instructions") if isinstance(config, dict) else None
    if not isinstance(instructions, list):
        problems.append("opencode.jsonc has no instructions list")
    elif not any(entry in accepted for entry in instructions):
        problems.append("opencode instructions do not include persona.md")

# Структура общих субагентов: отчёт одинаков в трёх харнессах, разделы ## совпадают в Claude и OpenCode.
def heading_lines(text: str, prefix: str) -> list[str]:
    # Строки, которые начинаются с prefix + " ", кроме строк внутри блоков кода.
    found: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and line.startswith(prefix + " "):
            found.append(line)
    return found


def sections(text: str) -> dict[str, str]:
    # Текст каждого раздела "## " до следующего такого же заголовка; строки блоков кода заголовками не считаются.
    out: dict[str, str] = {}
    name: str | None = None
    fenced = False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and line.startswith("## "):
            name = line[3:].strip()
            out[name] = ""
        if name is not None:
            out[name] += line
    return out


def section_problems(harness: str, texts: dict[str, str], section: str, group: list[str]) -> list[str]:
    # Раздел обязан быть у каждого агента группы и совпадать побайтно с первым агентом, где он есть.
    found = {a: sections(texts.get(a, "")).get(section) for a in group}
    present = [a for a in group if found[a] is not None]
    missing = [f"{harness}: section `{section}` missing in {a}" for a in group if found[a] is None]
    differs = [f"{harness}: section `{section}` of {a} differs from {present[0]}"
               for a in present[1:] if found[a] != found[present[0]]]
    return missing + differs


bodies = {
    "claude": {p.stem: p.read_text() for p in (C / "agents").glob("*.md")},
    "opencode": {p.stem: p.read_text() for p in (O / "agent").glob("*.md")},
    "codex": {p.stem: tomllib.loads(p.read_text()).get("developer_instructions", "")
              for p in (X / "agents").glob("*.toml")},
}
for name in sorted(base):
    ref = bodies["claude"][name]
    for h in ("codex", "opencode"):
        if name not in bodies[h]:
            continue  # отсутствие файла уже сообщает проверка набора агентов
        text = bodies[h][name]
        if heading_lines(text, "###") != heading_lines(ref, "###"):
            problems.append(f"{h}: {name} report contract ### headings differ from claude")
        if set(STATUS_TOKEN.findall(text)) != set(STATUS_TOKEN.findall(ref)):
            problems.append(f"{h}: {name} report contract status tokens differ from claude")
        if h == "opencode" and heading_lines(text, "##") != heading_lines(ref, "##"):
            problems.append(f"opencode: {name} section layout (## headings) differs from claude")
for h in ("claude", "opencode"):
    for names, group in SHARED_SECTIONS:
        for section in names:
            problems += section_problems(h, bodies[h], section, group)

print("agents:", sorted(base))
print("problems:", len(problems))
for p in problems:
    print(" -", p)
sys.exit(1 if problems else 0)
