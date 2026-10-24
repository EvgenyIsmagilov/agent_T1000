#!/usr/bin/env python3
"""What the config on disk costs in context — and what of it is dead weight.

`/context` shows the live window: MCP tool schemas, the system prompt, the
conversation. It cannot tell you which of your own skills earn their keep.
This does the other half, from disk.

The distinction that matters, and the one most audits get wrong: a skill's or
an agent's BODY is not in context. Only its `name` and `description` are —
those sit in the listing on every single turn. A 30 KB SKILL.md costs nothing
until something invokes it; a wordy description costs on every turn forever.
So the two are counted separately and only the first is a budget.

Read-only, stdlib only. Usage:
    python3 ~/.claude/tools/context-budget.py [--project DIR] [--verbose]
"""
import argparse
import re
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"

# Rough and deliberately crude: ~4 characters per token for mixed prose and
# identifiers. Good enough to rank components against each other, useless as
# an absolute number — `/context` is the absolute number.
CHARS_PER_TOKEN = 4

# A description past this is doing more than telling the model when to reach
# for the thing. Costs on every turn.
DESC_WORDS_WARN = 40

# `check-file-size.py` refuses a whole-file Read above this, so a body past it
# cannot be read in one go even by the agent that owns it.
BODY_LINES_WARN = 350

FRONTMATTER_KEY = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*)$")


def tokens(text):
    return len(text) // CHARS_PER_TOKEN


def human(n):
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


def frontmatter(path):
    """`name` and `description` from a Markdown front matter block.

    Hand-rolled instead of PyYAML: these tools stay stdlib-only, and the two
    keys needed here are flat strings. Folded values (`description: >-`) and
    plain wrapped continuations both fold into one line.
    """
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
        name = strip_quotes(fields.get("name", "")) or path.parent.name
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


def read_tokens(paths):
    total, rows = 0, []
    for path in paths:
        if not path.is_file():
            continue
        count = tokens(path.read_text(errors="replace"))
        rows.append((path, count))
        total += count
    return total, rows


def find_orphans(skills, texts):
    """Skills whose name appears nowhere else in the config.

    `texts` maps a source path to its content; a skill's own file is excluded
    before matching, because every SKILL.md names itself in its front matter
    and would otherwise always look referenced.

    Deliberately conservative in the other direction too: a name that is also
    an ordinary English word (`router`, `seo`, `taste`) will match some
    unrelated sentence and be counted as referenced. So this under-reports —
    everything it does name is worth a second look, but a clean run does not
    prove there is no cruft.
    """
    orphans = []
    for skill in skills:
        pattern = re.compile(r"(?<![\w-])" + re.escape(skill["name"]) + r"(?![\w-])")
        blob = "\n".join(text for path, text in texts.items() if path != skill["path"])
        if not pattern.search(blob):
            orphans.append(skill)
    return orphans


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=".", help="project root for its CLAUDE.md")
    parser.add_argument("--verbose", action="store_true", help="list every component")
    args = parser.parse_args()
    project = Path(args.project).resolve()

    skills = collect(CLAUDE_DIR / "skills", "*/SKILL.md")
    agents = collect(CLAUDE_DIR / "agents", "*.md")
    commands = collect(CLAUDE_DIR / "commands", "*.md")

    instruction_paths = [
        CLAUDE_DIR / "CLAUDE.md",
        project / "CLAUDE.md",
        project / ".claude" / "CLAUDE.md",
        *sorted((CLAUDE_DIR / "rules").glob("*.md")),
        *sorted((CLAUDE_DIR / "output-styles").glob("*.md")),
    ]
    instruction_total, instruction_rows = read_tokens(instruction_paths)

    # Claude Code names a project's directory after its path with every `/`
    # and `_` flattened to `-`.
    slug = re.sub(r"[/_]", "-", str(project))
    memory_dir = CLAUDE_DIR / "projects" / slug / "memory"
    memory_total, memory_rows = read_tokens([memory_dir / "MEMORY.md"])

    skills_always = sum(s["always"] for s in skills)
    agents_always = sum(a["always"] for a in agents)
    commands_always = sum(c["always"] for c in commands)
    always = instruction_total + memory_total + skills_always + agents_always + commands_always
    on_demand = sum(s["body"] for s in skills) + sum(a["body"] for a in agents)

    print(f"БЮДЖЕТ КОНТЕКСТА — {CLAUDE_DIR}, проект {project}\n")
    print(f"{'компонент':<26}{'шт':>4}{'постоянно':>12}{'по запросу':>13}")
    print(f"{'инструкции (CLAUDE.md, rules)':<26}{len(instruction_rows):>4}{human(instruction_total):>12}{'—':>13}")
    print(f"{'MEMORY.md (индекс)':<26}{len(memory_rows):>4}{human(memory_total):>12}{'—':>13}")
    print(f"{'навыки':<26}{len(skills):>4}{human(skills_always):>12}{human(sum(s['body'] for s in skills)):>13}")
    print(f"{'агенты':<26}{len(agents):>4}{human(agents_always):>12}{human(sum(a['body'] for a in agents)):>13}")
    print(f"{'команды':<26}{len(commands):>4}{human(commands_always):>12}{'—':>13}")
    print(f"{'ИТОГО':<26}{'':>4}{human(always):>12}{human(on_demand):>13}")

    heaviest = sorted(skills + agents, key=lambda i: -i["always"])[:8]
    print("\nСАМЫЕ ДОРОГИЕ ОПИСАНИЯ — платишь за них каждый ход")
    for item in heaviest:
        mark = " !" if item["words"] > DESC_WORDS_WARN else "  "
        print(f" {mark} {item['name']:<28}{item['words']:>4} слов  {human(item['always']):>6}")

    wordy = [i for i in skills + agents + commands if i["words"] > DESC_WORDS_WARN]
    empty = [i for i in skills if not i["description"]]
    huge = [s for s in skills if s["lines"] > BODY_LINES_WARN]

    print(f"\nНАХОДКИ")
    if wordy:
        # What trimming each one back to the threshold would return, at an
        # average ~6 characters per English word.
        budget = tokens("x" * DESC_WORDS_WARN * 6)
        saving = sum(max(tokens(i["description"]) - budget, 0) for i in wordy)
        print(f"  описание длиннее {DESC_WORDS_WARN} слов: {len(wordy)} шт."
              f" — сократив до порога, вернёшь ~{human(saving)} токенов на каждый ход")
    if empty:
        print(f"  без описания: {len(empty)} шт. — Claude их сам не выберет,"
              f" только вызовом по имени ({', '.join(i['name'] for i in empty[:6])})")
    if huge:
        print(f"  тело больше {BODY_LINES_WARN} строк: {len(huge)} шт."
              f" — check-file-size.py запретит прочитать целиком"
              f" ({', '.join(i['name'] for i in huge[:6])})")

    texts = {path: path.read_text(errors="replace")
             for path, _ in instruction_rows + memory_rows}
    for item in skills + agents + commands:
        texts[item["path"]] = item["path"].read_text(errors="replace")
    for path in sorted((CLAUDE_DIR / "hooks").glob("*.py")) + sorted((CLAUDE_DIR / "docs").glob("*.md")):
        texts[path] = path.read_text(errors="replace")
    orphans = find_orphans(skills, texts)
    if orphans:
        print(f"  ни разу не упомянуты нигде в конфиге: {len(orphans)} шт."
              f" — это «ты про них забыл», а не «они бесполезны»:"
              f" навык, который ты зовёшь по имени, ссылок и не требует")
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
    print("Схемы MCP-инструментов и системный промпт сюда не входят: они приходят"
          " не с диска. Живую картину даёт /context.")
    print("Тела навыков и агентов грузятся только при вызове — это не расход,"
          " а запас; сокращать их ради контекста бессмысленно.")


if __name__ == "__main__":
    main()
