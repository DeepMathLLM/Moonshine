---
name: verify-correctness-assumption
description: Check whether every condition and assumption involved in a subproblem has actually been used in the solution process; the claim sent for verification must be a complete standalone statement with all conditions and a clear conclusion.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: verify_correctness_assumption query_memory search_knowledge
metadata:
  title: Verify Correctness Assumption
  category: builtin
  tags: research,verification,assumptions
  skill-standard: agentskills.io/v1
---

# Verify Correctness Assumption

## Usage Hint
- Use this skill to check whether a proof uses exactly the stated assumptions and definitions.
- Use it when hidden hypotheses, domain issues, exceptional cases, or mismatched definitions are the main risk.
- Before calling a verification tool, formulate the claim or conclusion as a complete standalone statement, including all relevant hypotheses, definitions, parameter restrictions, domains, and the exact conclusion to be checked.

## Summary
- Use this skill when the main question is whether the solution really uses every stated condition and assumption.
- This is a single-dimension check only. It does not replace full verification.
- Actual verification evidence appears only after calling `verify_correctness_assumption`.

## Execution Steps
1. Restate the target subproblem as a complete standalone statement with all known conditions and a clear target conclusion.
2. Use `query_memory` or `search_knowledge` when prior verifier reports, assumptions, or related lemmas are needed.
3. Call `verify_correctness_assumption` on the full solution process.
4. Treat the result as passed only when the tool returns `overall_verdict = assumption_correct`.
5. If the result is `assumption_incorrect`, route the branch to correction or failure analysis rather than treating the claim as established.

## Tool Calls
- `verify_correctness_assumption`: Run the configured pessimistic assumption-usage check.
- `query_memory`: Retrieve prior assumptions, failed verifier reports, or related branch context.
- `search_knowledge`: Retrieve reusable lemmas or definitions when the assumption set depends on prior knowledge.

## File References
- `projects/<project_slug>/workspace/blueprint.md`
- `projects/<project_slug>/memory/research_log.jsonl`
- `projects/<project_slug>/memory/by_type/verification.md`
- `projects/<project_slug>/memory/research_log_index.sqlite`

## Output Contract
- Return the tool result, including reviewer reports, failed reviewers, aggregated errors, and the final assumption verdict.
- Free-text discussion is not equivalent to the tool result.
