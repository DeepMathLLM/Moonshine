---
name: verify-correctness-computation
description: Check for calculation errors in the solution process of a subproblem.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: verify_correctness_computation query_memory search_knowledge
metadata:
  title: Verify Correctness Computation
  category: builtin
  tags: research,verification,calculation
  skill-standard: agentskills.io/v1
---

# Verify Correctness Computation

## Usage Hint
- Use this skill to check algebraic, analytic, numerical, or symbolic computations inside an argument.
- Use it when the proof depends on formulas, estimates, reductions, transformations, or calculated examples.

## Summary
- Use this skill when the main question is whether the solution process contains any calculation error.
- This is a single-dimension check only. It does not replace full verification.
- Actual verification evidence appears only after calling `verify_correctness_computation`.

## Execution Steps
1. Restate the target subproblem and the full solution process to be checked.
2. Use `query_memory` or `search_knowledge` when prior computation failures, formulas, or referenced lemmas are needed.
3. Call `verify_correctness_computation` on the full solution process.
4. Treat the result as passed only when the tool returns `overall_verdict = calculation_correct`.
5. If the result is `calculation_incorrect`, route the branch to correction or failure analysis rather than treating the claim as established.

## Tool Calls
- `verify_correctness_computation`: Run the configured pessimistic computation check.
- `query_memory`: Retrieve prior computation failures, branch notes, or relevant local context.
- `search_knowledge`: Retrieve stored formulas, lemmas, or definitions that affect the calculation steps.

## File References
- `projects/<project_slug>/workspace/blueprint.md`
- `projects/<project_slug>/memory/research_log.jsonl`
- `projects/<project_slug>/memory/by_type/verification.md`
- `projects/<project_slug>/memory/research_log_index.sqlite`

## Output Contract
- Return the tool result, including reviewer reports, failed reviewers, aggregated errors, and the final calculation verdict.
- Free-text discussion is not equivalent to the tool result.
