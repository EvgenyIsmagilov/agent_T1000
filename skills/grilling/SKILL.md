---
name: grilling
description: Stress-test a plan or design by interrogating its open decisions before building. Use only ahead of non-trivial, ambiguous, or high-risk work where a wrong plan is costly, or when the user explicitly asks to "grill" a plan. Do not use for small, clear, or low-risk tasks.
---

Use this before building something non-trivial, ambiguous, or costly to get wrong. First judge whether grilling is warranted: skip it entirely for small, clear, or low-risk work — interrogating a trivial or already-settled plan is noise, not diligence.

Grill only the genuinely open, consequential decisions. Do not re-litigate what is already decided or obvious, and never manufacture questions to appear thorough. If nothing material is actually open, say so and proceed instead of inventing an interview.

When it is warranted, interrogate the plan until you and the user reach a shared understanding:

- Walk down each branch of the design tree, resolving dependencies between decisions one at a time.
- Ask one question at a time and wait for the answer before the next — several at once is bewildering.
- For each question, give your recommended answer, not just the question.
- If a *fact* can be found by exploring the codebase, look it up yourself rather than asking. The *decisions* are the user's — put each one to them and wait.

Do not enact the plan until the user confirms shared understanding.
