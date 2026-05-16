---
name: verify-overall
description: Verify the correctness of a mathematical solution by running pessimistic assumption, computation, and logic checks and requiring all three to pass; the claim sent for verification must be a complete standalone statement with all conditions and a clear conclusion.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: verify_overall verify_correctness_assumption verify_correctness_computation verify_correctness_logic query_memory search_knowledge
metadata:
  title: Verify Overall
  category: builtin
  tags: research,verification,correctness
  skill-standard: agentskills.io/v1
---

# Verify Overall

## Usage Hint
- Use this skill to run a full correctness review of an important claim, proof, or final result.
- Use it when the result is important enough to require assumption, computation, and logic checks together.
- Before calling a verification tool, formulate the claim or conclusion as a complete standalone statement, including all relevant hypotheses, definitions, parameter restrictions, domains, and the exact conclusion to be checked.

## Summary
- Use this skill when an important solve step, subproblem solution, reusable conclusion, or final blueprint needs a full correctness gate.
- `verify_overall` is the default verification path in the current research workflow.
- The skill is complete only when `verify_overall` has been called and its tool result is available.
- Omit `review_count` unless you intentionally want to override the configured default reviewer count.

## Execution Steps
1. Restate the target subproblem or claim as a complete standalone statement with all known conditions and a clear target conclusion, then include the solution process to be checked.
2. Use `query_memory` or `search_knowledge` when prior verifier reports, assumptions, definitions, or branch context are needed.
3. Call `verify_overall`. Do not pass `review_count` unless you intentionally need a non-default reviewer count.
4. `verify_overall` must run all three dimensions:
   - `verify_correctness_assumption`
   - `verify_correctness_computation`
   - `verify_correctness_logic`
5. Treat the result as passed only when the tool returns `overall_verdict = correct`.
6. If any dimension fails, treat the solution as incorrect and route the branch to correction, failure analysis, or reformulation.

## Tool Calls
- `verify_overall`: Run the full multidimensional pessimistic verifier and aggregate the result.
- `verify_correctness_assumption`: Use directly when only the assumption-usage dimension needs targeted checking.
- `verify_correctness_computation`: Use directly when only the calculation dimension needs targeted checking.
- `verify_correctness_logic`: Use directly when only the logic dimension needs targeted checking.
- `query_memory`: Retrieve prior verifier reports, failed paths, or branch context.
- `search_knowledge`: Retrieve reusable lemmas, theorem statements, or definitions used in the audited solution.

## File References
- `projects/<project_slug>/workspace/blueprint.md`
- `projects/<project_slug>/memory/research_log.jsonl`
- `projects/<project_slug>/memory/by_type/verification.md`
- `projects/<project_slug>/memory/research_log_index.sqlite`

## Output Contract
- Return the `verify_overall` result, including the three dimension results and the final aggregate verdict.
- Only a passed `verify_overall` result counts as full verification evidence for promotion or final acceptance.
