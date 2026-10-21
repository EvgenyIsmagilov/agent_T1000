# Orchestration diagrams

Two views of the same system — see [orchestration-architecture.md](orchestration-architecture.md)
for the rationale behind each piece. Rendered as Mermaid; any editor/viewer
with Mermaid support (VS Code, GitHub, etc.) shows these as diagrams.

## 1. Static structure — roles, tiers, hooks

What exists and what watches what. Not a sequence — just the map.

```mermaid
flowchart TD
    ORC[["Orchestrator<br/>(main agent)"]]

    subgraph Gates["Gates — PreToolUse"]
        RO["require-orchestration.py<br/>on Agent: refuses to delegate until<br/>task-orchestration is loaded this session"]
        CFS["check-file-size.py<br/>on Read: refuses a whole-file read &gt;350 lines"]
        TG["trino-guardrail.py<br/>on Trino execute/explain: text guard on DDL/DML"]
    end

    subgraph Assess["Assessment"]
        CTA["code-tier-assessment skill<br/>closed-list criteria → T1 / T2 / T3"]
    end

    subgraph Exec["Executors — code-writer tiers"]
        T1["code-writer-t1<br/>haiku · max effort<br/>~30% of slices"]
        T2["code-writer-t2<br/>sonnet · medium effort<br/>~50%, default"]
        T3["code-writer-t3<br/>sonnet · max effort<br/>~20% of slices"]
    end

    subgraph Validate["Validation & review"]
        TR["test-runner<br/>haiku · low"]
        BI["bug-investigator<br/>sonnet · high<br/>cause unknown → diagnoses it"]
        CR["reviewer<br/>opus · xhigh<br/>code-review + sql-review / appsec-review as needed<br/>cause already found → fix goes straight to executor"]
    end

    subgraph Research["Research & search (read-only)"]
        WRF["web-researcher<br/>haiku · low"]
        DRD["docs-researcher<br/>sonnet · high"]
        EXP["Explore (built-in)"]
        PLN["planner<br/>sonnet · high"]
    end

    subgraph Obs["Observability — SubagentStop"]
        LS["log-subagent.py<br/>one JSONL line per finished run"]
        LOG[("subagent-runs.jsonl")]
        AS["agent-stats.py<br/>cost & format digest"]
        FP["first-pass.py<br/>% closed without retry, by tier"]
    end

    subgraph DocsDrift["Docs drift"]
        DDR["docs-drift-reminder.py<br/>PostToolUse on Edit/Write"]
        ARCH[["orchestration-architecture.md"]]
    end

    ORC -->|"Agent(...)"| RO
    ORC -->|"Read(...)"| CFS
    ORC -->|"mcp__trino__*"| TG
    ORC --> CTA
    CTA -->|T1| T1
    CTA -->|T2, default| T2
    CTA -->|T3| T3

    ORC --> TR
    ORC --> BI
    ORC --> CR
    ORC --> WRF
    ORC --> DRD
    ORC --> EXP
    ORC --> PLN

    T1 & T2 & T3 & TR & BI & CR & WRF & DRD & PLN -.->|every subagent run| LS
    LS --> LOG
    LOG --> AS
    LOG --> FP
    AS -.->|final response ledger| ORC
    FP -.->|calibrate tier targets| CTA

    ORC -->|"Edit/Write on agents/*.md,<br/>task-orchestration, code-tier-assessment,<br/>log-subagent.py, require-orchestration.py,<br/>agent-stats.py, first-pass.py"| DDR
    DDR -.->|"reminder (once per session+file)"| ORC
    DDR -.->|"update if significant"| ARCH
```

## 2. Temporal flow — one slice, with escalation

Only real subagents get their own lane — a subagent is the only thing here
that actually receives a message and sends one back. `code-tier-assessment`
is a skill the orchestrator applies to itself (a checklist, no round trip),
and the logging hook fires automatically after every lane below finishes on
its own, without being asked — both are called out as notes instead of
lanes, so the diagram shows the *decisions*, not every moving part at once.

```mermaid
sequenceDiagram
    autonumber
    participant O as Orchestrator
    participant CW as code-writer-tN
    participant TR as test-runner
    participant BI as bug-investigator
    participant CR as reviewer

    Note over O: picks a tier itself, via the code-tier-assessment<br/>checklist — not a subagent call

    O->>CW: brief (goal, files, acceptance criteria, tier)
    CW-->>O: implemented + self-verified

    O->>TR: run the closing check
    TR-->>O: PASS or FAIL

    alt checks fail
        Note over TR,BI: cause not known yet — that's the job here
        O->>BI: find the root cause
        BI-->>O: diagnosis: capability, bad brief, flaky env, or slice too big
        Note over O: capability → retry one tier higher · anything else → retry same tier
        O->>CW: revised brief
    else checks pass
        O->>CR: review the diff
        CR-->>O: APPROVE or REQUEST_CHANGES
        alt changes requested
            Note over CR,CW: finding already carries evidence + fix direction —<br/>nothing left to diagnose, so no bug-investigator step
            O->>CW: fix, same tier, using the reviewer's own findings verbatim
        else approved
            Note over O: slice closed
        end
    end

    Note over O: every call above is logged the moment it finishes —<br/>a hook, always on, not a step anyone asks for (see diagram 1)
```
