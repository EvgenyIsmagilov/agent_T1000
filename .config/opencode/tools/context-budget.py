#!/usr/bin/env python3
"""What the OpenCode config on disk costs in context — and what is dead weight.

Ported from ~/.claude/tools/context-budget.py, trimmed to what OpenCode
actually has. Dropped on purpose, not by oversight:
  - CLAUDE.md / rules / output-styles / project MEMORY.md — OpenCode has no
    equivalent instruction-file or per-project-memory mechanism to measure;
  - commands as separate files — OpenCode commands live inline as `template`
    strings under `command` in opencode.jsonc, not as their own Markdown files.
Kept and OpenCode-only: skills are filtered through `permission.skill` in
opencode.jsonc (allow/deny by name) before counting — a skill denied there
costs nothing, on-demand or otherwise, and is reported separately.

Same caveat as upstream: the body of a skill or agent is NOT always in
context, only its name+description are (shown in the listing every turn).
Counted separately; only the first is a real budget.

Read-only, stdlib only. Usage:
    python3 ~/.config/opencode/tools/context-budget.py [--verbose]
"""
import argparse
import json
import re
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
OPENCODE_DIR = Path.home() / ".config" / "opencode"
CONFIG_PATH = OPENCODE_DIR / "opencode.jsonc"

CHARS_PER_TOKEN = 4
DESC_WORDS_WARN = 40
# claude-hooks.js denies a whole-file `read` above this (OPENCODE_READ_LINE_LIMIT, default 350).
BODY_LINES_WARN = 350

FRONTMATTER_KEY = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*)$")


def tokens(text):
    return len(text) // CHARS_PER_TOKEN


def human(n):
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


def frontmatter(path):
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return {}, ""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    block, body = text[3:end], text[end + 4:]

    fields, key = {}, None
    for line in block.splitlines():
        if not line.strip():
            continue
        match = FRONTMATTER_KEY.match(line)
        if match and not line[0].isspace():
            key, value = match.group(1), match.group(2).strip()
            fields[key] = "" if value in (">", "|", ">-", "|-") else value
        elif key:
            fields[key] += " " + line.strip()
    return fields, body


def strip_quotes(value):
    value = value.strip()
    if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def collect(directory, pattern):
    items = []
    for path in sorted(directory.glob(pattern)):
        fields, body = frontmatter(path)
        name = strip_quotes(fields.get("name", "")) or path.stem
        description = strip_quotes(fields.get("description", ""))
        items.append({
            "name": name,
            "path": path,
            "description": description,
            "words": len(description.split()),
            "always": tokens(name + description),
            "body": tokens(body),
            "lines": body.count("\n") + 1,
        })
    return items


def load_jsonc(path):
    """Strip `//` line comments well enough for this one config file.

    Not a real JSONC parser — a `//` inside a string value would be
    mishandled. opencode.jsonc does not currently use one; if that changes,
    this needs a proper tokenizer instead of getting cleverer here.
    """
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return {}
    cleaned = re.sub(r"(?m)^\s*//.*$", "", text)
    cleaned = re.sub(r"//[^\n\"]*$", "", cleaned, flags=re.M)
    # Stripping a `// comment,` line can leave a dangling trailing comma
    # before `}`/`]` — invalid JSON even though it was valid JSONC.
    cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
    try:
        return json.loads(cleaned)
    except ValueError:
        return {}


def allowed_skill_names(config):
    """Names explicitly `allow`-ed under permission.skill, minus the `*` wildcard."""
    rules = ((config.get("permission") or {}).get("skill")) or {}
    return {name for name, decision in rules.items() if name != "*" and decision == "allow"}


def configured_commands(config):
    items = []
    for name, spec in (config.get("command") or {}).items():
        if not isinstance(spec, dict):
            continue
        description = str(spec.get("description") or "")
        template = str(spec.get("template") or "")
        items.append({
            "name": name,
            "description": description,
            "words": len(description.split()),
            "always": tokens(name + description),
            "body": tokens(template),
        })
    return items


def find_orphans(skills, texts):
    orphans = []
    for skill in skills:
        pattern = re.compile(r"(?<![\w-])" + re.escape(skill["name"]) + r"(?![\w-])")
        blob = "\n".join(text for path, text in texts.items() if path != skill["path"])
        if not pattern.search(blob):
            orphans.append(skill)
    return orphans


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true", help="list every component")
    args = parser.parse_args()

    config = load_jsonc(CONFIG_PATH)
    allowed = allowed_skill_names(config)

    all_skills = collect(CLAUDE_DIR / "skills", "*/SKILL.md")
    skills = [s for s in all_skills if s["name"] in allowed] if allowed else all_skills
    denied_count = len(all_skills) - len(skills)

    agents = collect(OPENCODE_DIR / "agent", "*.md")
    commands = configured_commands(config)

    skills_always = sum(s["always"] for s in skills)
    agents_always = sum(a["always"] for a in agents)
    commands_always = sum(c["always"] for c in commands)
    always = skills_always + agents_always + commands_always
    on_demand = sum(s["body"] for s in skills) + sum(a["body"] for a in agents)

    print(f"БЮДЖЕТ КОНТЕКСТА — {OPENCODE_DIR}\n")
    print(f"{'компонент':<26}{'шт':>4}{'постоянно':>12}{'по запросу':>13}")
    print(f"{'навыки (после permission)':<26}{len(skills):>4}{human(skills_always):>12}{human(sum(s['body'] for s in skills)):>13}")
    print(f"{'агенты':<26}{len(agents):>4}{human(agents_always):>12}{human(sum(a['body'] for a in agents)):>13}")
    print(f"{'команды (opencode.jsonc)':<26}{len(commands):>4}{human(commands_always):>12}{'—':>13}")
    print(f"{'ИТОГО':<26}{'':>4}{human(always):>12}{human(on_demand):>13}")
    if denied_count:
        print(f"\nОтфильтровано permission.skill: {denied_count} из {len(all_skills)} навыков в ~/.claude/skills недоступны этому агенту.")

    heaviest = sorted(skills + agents, key=lambda i: -i["always"])[:8]
    print("\nСАМЫЕ ДОРОГИЕ ОПИСАНИЯ — платишь за них каждый ход")
    for item in heaviest:
        mark = " !" if item["words"] > DESC_WORDS_WARN else "  "
        print(f" {mark} {item['name']:<28}{item['words']:>4} слов  {human(item['always']):>6}")

    wordy = [i for i in skills + agents + commands if i["words"] > DESC_WORDS_WARN]
    empty = [i for i in skills if not i["description"]]
    huge = [s for s in skills + agents if s["lines"] > BODY_LINES_WARN]

    print(f"\nНАХОДКИ")
    if wordy:
        budget = tokens("x" * DESC_WORDS_WARN * 6)
        saving = sum(max(tokens(i["description"]) - budget, 0) for i in wordy)
        print(f"  описание длиннее {DESC_WORDS_WARN} слов: {len(wordy)} шт."
              f" — сократив до порога, вернёшь ~{human(saving)} токенов на каждый ход")
    if empty:
        print(f"  без описания: {len(empty)} шт. — модель их сама не выберет,"
              f" только вызовом по имени ({', '.join(i['name'] for i in empty[:6])})")
    if huge:
        print(f"  тело больше {BODY_LINES_WARN} строк: {len(huge)} шт."
              f" — file-read guardrail в claude-hooks.js запретит прочитать целиком"
              f" ({', '.join(i['name'] for i in huge[:6])})")

    texts = {item["path"]: item["path"].read_text(errors="replace") for item in skills + agents}
    for path in sorted((OPENCODE_DIR / "plugins").glob("*.js")):
        texts[path] = path.read_text(errors="replace")
    texts[CONFIG_PATH] = CONFIG_PATH.read_text(errors="replace") if CONFIG_PATH.is_file() else ""
    orphans = find_orphans(skills, texts)
    if orphans:
        print(f"  ни разу не упомянуты нигде в конфиге: {len(orphans)} шт."
              f" — это «про них забыли», а не «они бесполезны»:"
              f" навык, который зовут по имени, ссылок и не требует")
        for item in sorted(orphans, key=lambda i: -i["body"])[:12]:
            print(f"      {item['name']:<30}{item['lines']:>5} строк")
    if not (wordy or empty or huge or orphans):
        print("  чисто")

    if args.verbose:
        print("\nВСЕ НАВЫКИ — постоянно / тело")
        for item in sorted(skills, key=lambda i: -i["always"]):
            print(f"  {item['name']:<32}{human(item['always']):>7}{human(item['body']):>9}"
                  f"{item['words']:>6} слов")

    print("\nЧисла — оценка по ~4 символа на токен, годится для сравнения"
          " компонентов между собой, не как абсолют.")
    print("Схемы MCP-инструментов и системный промпт сюда не входят.")
    print("Тела навыков и агентов грузятся только при вызове — это не расход,"
          " а запас; сокращать их ради контекста бессмысленно.")


if __name__ == "__main__":
    main()
