---
name: quality-assessor
description: Evaluate candidate research problems by impact, feasibility, novelty, and richness.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: query_memory search_knowledge assess_problem_quality
metadata:
  title: Quality Assessor
  category: builtin
  tags: research,evaluation,question-design
  skill-standard: agentskills.io/v1
---

# Quality Assessor

## Usage Hint
- Use this skill to evaluate whether a candidate research problem is worth entering problem solving.
- Use it when the project is at the problem-design gate, especially before treating a proposed problem as the active problem.

## Summary
- Score candidate research questions before committing to a proof-search path.
- Use the weighted dimensions: Impact 40%, Feasibility 25%, Novelty 20%, Richness 15%.

## Execution Steps
1. Restate the candidate problem and its assumptions.
2. Evaluate theoretical impact and possible applications.
3. Estimate feasibility under current resources and known tools.
4. Check novelty against local memory and known references.
5. Assess richness: extensions, subproblems, and follow-up value.
6. Recommend accept, refine, defer, or reject.
7. Call `assess_problem_quality` once for the candidate that should control the stage gate.
8. A passed tool result is the dedicated active-problem review.
9. `## Problem Review` remains a clear fallback format for human-readable explanation, but the gate expects the dedicated quality-assessor review evidence.
10. If the candidate is genuinely ready for solving and an active problem already exists, say so plainly and proceed into solving-oriented work in subsequent turns.
11. This review is intended to run once per transition attempt unless the problem itself changes materially.

## Tool Calls
- `query_memory`: Compare with prior project decisions and failed directions.
- `search_knowledge`: Check overlap with stored conclusions.
- `assess_problem_quality`: Run the dedicated one-pass stage-gate review and persist it as `problem_review`.

## File References
- `projects/<project_slug>/memory/by_type/problem.md`
- `projects/<project_slug>/memory/by_type/research_note.md`
- `projects/<project_slug>/memory/research_log_index.sqlite`

## Output Contract
- Natural language is fine.
- When the review passes and should govern stage progression, use `assess_problem_quality` so the dedicated review is explicit.
- When the review supports entering solving, say that clearly and let the subsequent turns continue with solving-oriented work.
- If the review settles a durable design decision, state that decision explicitly in the review itself.

## Notes
- Penalize vague questions even if they are exciting.
