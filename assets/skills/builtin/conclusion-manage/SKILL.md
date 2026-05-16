---
name: conclusion-manage
description: Structure lemmas, propositions, and verified conclusions with clear status and evidence.
compatibility: Requires an Agent Skills-compatible runtime that exposes structured knowledge-storage tools.
allowed-tools: search_knowledge query_memory verify_overall verify_correctness_assumption verify_correctness_computation verify_correctness_logic
metadata:
  title: Conclusion Manage
  category: builtin
  tags: knowledge,lemmas,conclusions
  skill-standard: agentskills.io/v1
---

# Conclusion Manage

## Usage Hint
- Use this skill to decide whether a mathematical result is stable enough to rely on later.
- Use it when a claim has been proved, checked, or compared with prior work and needs disciplined reuse.

## Summary
- Convert stable research results into clear, reusable statements.
- Keep reusable results concise, traceable, and explicitly scoped.

## Execution Steps
1. Decide whether the result is stable enough to reuse later.
2. Express the result as a clear title and statement, with a short proof sketch or evidence summary.
3. If verification is still missing, keep the result as a project-level candidate rather than forcing it into formal knowledge.
4. Use available retrieval before duplicating a conclusion.
5. Search existing knowledge first if duplication is likely.

## Tool Calls
- `search_knowledge`: Check whether a similar result already exists.
- `query_memory`: Recover supporting evidence before presenting a conclusion when needed.
- `verify_overall`: Check the conclusion before promotion when correctness matters.
- `verify_correctness_assumption`, `verify_correctness_computation`, `verify_correctness_logic`: Use only when one verification dimension needs targeted diagnosis before or after the full gate.

## File References
- `projects/<project_slug>/memory/research_log_index.sqlite`
- `knowledge/entries/*.md`
- `projects/<project_slug>/memory/by_type/verified_conclusion.md`

## Output Contract
- Natural language is fine.
- The conclusion should have a clear title, statement, evidence summary, and status.
- Unverified claims remain project-level candidates.

## Notes
- Prefer the knowledge layer for stable conclusions rather than leaving them only in dynamic project notes.
