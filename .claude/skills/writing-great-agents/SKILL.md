---
name: writing-great-agents
description: Reference for writing and editing subagents well — the routing description, the tool budget, and the report contract that keeps a second context honest.
disable-model-invocation: true
---

A skill shapes the context you are already in. A subagent buys a **second context** — a window the parent never sees — and spends it on one thing: the single message it sends back. **The report is the deliverable**; the work is only how the report got written. Every lever below serves that.

Shared vocabulary (**description**, **context load**, **no-op**, **negation**, **premature completion**, **leading word**) is defined in [`../writing-great-skills/GLOSSARY.md`](../writing-great-skills/GLOSSARY.md). Field-by-field frontmatter lives in [`FRONTMATTER.md`](FRONTMATTER.md); a skeleton to copy lives in [`TEMPLATE.md`](TEMPLATE.md).

## When a subagent earns its context

Spawn one when:

- the work generates **noise** the parent must not carry — full test logs, twenty file reads, a search across two hundred files;
- the work needs a different **stance** than the parent's — adversarial review, read-only investigation. A separate context with a trimmed toolset makes "never edits code" structural instead of aspirational;
- independent slices can run at once.

Skip it when the parent needs the raw material verbatim: you would pay a spawn to hand the same tokens straight back. And a subagent is **mute** — stateless, unable to ask a clarifying question, silent until it returns. Work that needs a round-trip with the human stays in the parent.

## The routing description

The description is the one thing the parent sees at dispatch time, loaded every turn for every agent — the same **context load** a model-invoked skill pays. Three clauses, in order:

1. **What it returns.** Name the product, not the identity: "returns an evidence-backed root-cause diagnosis" beats "is a debugging expert".
2. **When to reach for it.** One trigger per branch. Add "Use proactively after…" only when it should fire unasked.
3. **The boundary.** Where it must not be sent, and what it will not do — "Read-only: never edits code or implements fixes."

That third clause is a **negation**, and here negation is correct. In a body, prohibition backfires: it names the elephant. In a description, it is a guardrail for the _caller_ — it prunes the dispatcher's option set and sets the parent's expectation of what comes back. Negate in the description; prompt the positive in the body.

When several agents cover one domain, the descriptions must **route against each other**: each names its own band and hands off by name ("escalate to code-writer-t2"). Two agents whose descriptions read alike produce a coin flip at dispatch.

## The tool budget

Omit `tools` and the agent inherits everything — including `Agent`, so it can spawn further agents. An allowlist is the cheapest guardrail you have; write one.

- Read-only stance: `Read, Grep, Glob, Bash, Skill`. `Bash` is the hole — it can write, move, and push. The body's prohibitions carry what the allowlist cannot.
- Writing stance: add `Edit, Write`. Withhold commit/push/deploy in the body, since they all arrive through `Bash`.
- Keep `Skill` whenever the agent should obey project standards; without it, your `python`/`sql`/`git` skills are out of its reach.
- No `Agent` tool means no delegating. Anything the parent would hand off, this agent must do itself — including working around a hook (the 350-line whole-file `Read` block needs `offset`/`limit`, not a subagent). Say so in the body.
- `disallowedTools` is applied _before_ `tools`; a tool named in both is removed.

## The budget triad

`model`, `effort`, and `maxTurns` are picked together — a cheap model with a huge turn budget just fails slowly. Calibration from the agents in `~/.claude/agents/`:

| Shape | model | effort | maxTurns |
|---|---|---|---|
| Fast lookup, fixed output | haiku | low | 12 |
| Mechanical runner | haiku | low | 20 |
| Investigation, one hypothesis at a time | sonnet | high | 30 |
| Ordinary implementation | sonnet | medium | 80 |
| Critical implementation, many edits | sonnet | max | 110 |
| Adversarial review | opus | max | 45 |

`maxTurns` is a hard stop: the run is marked partial and whatever the agent had planned to say never arrives. Because the agent cannot see its own remaining budget, the body must instruct it to return `PARTIAL` the moment the work outgrows the run. A report that never arrives tells the parent nothing at all.

## The report contract

The parent reads one message and nothing else, so specify it exactly:

- **Fixed headings**, named in the body, in order.
- **A status token** on the first line — `PASS` / `FAIL` / `PARTIAL` / `BLOCKED`, or the verdict vocabulary the domain needs — so the parent can branch without parsing prose.
- **"Begin with the heading itself"** — no preamble, no greeting, no summary sentence before it. Without this line agents open with a paragraph of throat-clearing.
- **A word cap**, plus a spill route: when the detail genuinely exceeds the cap and the brief named a scratchpad path, write the bulk there and cite the path in one line.
- **Noise limits**, spelled out: no passing-test names, no full logs, no repeating the task back, no generic advice.

Everything the cap excludes stays in the agent's own window, which is the point of having spawned it.

## Steering the body

- Open with one sentence of stance ("You are a focused, read-only test execution subagent"), then go straight to rules. Roleplay past that sentence is a **no-op**.
- Make each rule **checkable**: "record every executed command and its exit code" survives a run; "be thorough" does not.
- State the **blocked path**: when the agent hits a missing permission, credential, or dependency, it reports the blocker precisely rather than improvising around it. Silent improvisation is the expensive failure, because the parent cannot see it happen.
- Declare the invocation **stateless** — no memory of previous runs, no assumptions carried in.
- Pin the **output language** ("in the language of the delegated task") when the project is not English-only.

## Failure modes

- **Orphan** — an agent nothing ever routes to. The description failed, not the body. Test by asking whether a parent holding only the description would pick it for the case you built it for.
- **Silent runner** — the run ends with no usable report: turns exhausted, or the agent decided the work spoke for itself. Cured by the status token plus the explicit `PARTIAL` instruction.
- **Context dump** — the agent returns everything it read, so the parent pays the tokens the spawn was meant to save. Cured by the word cap and the noise limits.
- **Stance drift** — a read-only agent "just fixes" the bug it found. Cured by the toolset first, the body's prohibitions second.
- **Twin** — two agents whose descriptions overlap, so dispatch is a coin flip. Merge them, or cut each description until the bands are disjoint.
