---
name: propose-subgoal-decomposition
description: Break a mathematical problem into multiple viable subgoal plans.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: query_memory search_knowledge
metadata:
  title: Propose Subgoal Decomposition
  category: builtin
  tags: research,proof,subgoals
  skill-standard: agentskills.io/v1
---

# Propose Subgoal Decomposition

## Usage Hint
- Use this skill to break a large theorem or research question into smaller subgoals.
- Use it when the main target is too broad to attack directly or needs a dependency structure.

## Summary
- Generate several materially different decomposition plans for the current problem.
- Preserve plan assumptions, dependencies, and failure risks.

## Execution Steps
1. Restate the target theorem or research question.
2. Gather relevant examples, counterexamples, failed paths, and known results.
3. Propose multiple decomposition plans.
4. For each plan, list ordered subgoals and the expected proof strategy.
5. Identify likely bottlenecks and evidence needed to screen the plan.

## Tool Calls
- `query_memory`: Recover prior subgoals, failed paths, and branch states.
- `search_knowledge`: Reuse stored lemmas and theorem summaries.

## File References
- `projects/<project_slug>/memory/by_type/verified_conclusion.md`
- `projects/<project_slug>/memory/by_type/problem.md`
- `projects/<project_slug>/memory/by_type/research_note.md`

## Output Contract
- Return decomposition plans with subgoals, dependencies, risks, and a recommended first plan.
- If one plan becomes the working plan, state it clearly; the resulting plan is clear enough for later retrieval.

## Notes
- Do not hide a hard part inside a vague subgoal.
