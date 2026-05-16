---
name: problem-solver
description: Work on one selected research problem or branch using available retrieval, skills, and solve attempts.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: query_memory search_knowledge verify_overall verify_correctness_assumption verify_correctness_computation verify_correctness_logic
metadata:
  title: Problem Solver
  category: builtin
  tags: research,solver,proof
  skill-standard: agentskills.io/v1
---

# Problem Solver

## Usage Hint
- Use this skill to work on an accepted active research problem.
- Use it when the problem is clear enough for solving and the next step is a concrete proof, computation, construction, or branch analysis.

## Summary
- Push one selected problem branch forward with concrete reasoning.
- Use this as a lightweight stand-in for future dedicated solver subagents.

## Execution Steps
1. State the branch objective and current assumptions.
2. Retrieve relevant memory and known results.
3. Attempt a proof route, example analysis, or counterexample search.
4. State branch progress, failures, and next actions clearly.
5. Keep accepted claims, partial arguments, and open gaps separate.

## Tool Calls
- `query_memory`: Recover branch context and prior attempts.
- `search_knowledge`: Reuse known mathematical conclusions.
- `verify_overall`: Verify an important branch claim or solve step before promoting it.
- `verify_correctness_assumption`, `verify_correctness_computation`, `verify_correctness_logic`: Use only when one verification dimension needs targeted diagnosis.

## File References
- `projects/<project_slug>/memory/by_type/research_note.md`
- `projects/<project_slug>/memory/by_type/verified_conclusion.md`
- `projects/<project_slug>/memory/research_log_index.sqlite`

## Output Contract
- Return a branch progress report with solved parts, blockers, and next actions.
- State branch status, solve attempts, blockers, and next actions clearly.
- Prior branch progress can be retrieved from `research_log.jsonl` when relevant.
- Before treating a claim as verified, run `verify_overall`; otherwise keep the result as partial progress.

## Notes
- Future versions may delegate this skill to a true subagent.
