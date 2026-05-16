---
name: verify-correctness-logic
description: Check for logical errors and flaws in the solution process of a subproblem; the claim sent for verification must be a complete standalone statement with all conditions and a clear conclusion.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: verify_correctness_logic query_memory search_knowledge
metadata:
  title: Verify Correctness Logic
  category: builtin
  tags: research,verification,logic
  skill-standard: agentskills.io/v1
---

# Verify Correctness Logic

## Usage Hint
- Use this skill to check the logical structure of a proof or derivation.
- Use it when implications, equivalences, quantifiers, case splits, or dependency ordering are the main risk.
- Before calling a verification tool, formulate the claim or conclusion as a complete standalone statement, including all relevant hypotheses, definitions, parameter restrictions, domains, and the exact conclusion to be checked.

## Summary
- Use this skill when the main question is whether the solution process contains logical errors, gaps, or invalid transitions.
- This is a single-dimension check only. It does not replace full verification.
- Actual verification evidence appears only after calling `verify_correctness_logic`.

## Execution Steps
1. Restate the target subproblem as a complete standalone statement with all assumptions and the clear conclusion the solution claims to establish.
2. Use `query_memory` or `search_knowledge` when prior verifier reports, cited lemmas, or branch context are needed.
3. Call `verify_correctness_logic` on the full solution process.
4. Treat the result as passed only when the tool returns `overall_verdict = logic_correct`.
5. If the result is `logic_incorrect`, route the branch to correction or failure analysis rather than treating the claim as established.

## Tool Calls
- `verify_correctness_logic`: Run the configured pessimistic logic check.
- `query_memory`: Retrieve prior logic failures, branch notes, or reasoning context.
- `search_knowledge`: Retrieve reusable lemmas, theorem statements, or definitions needed to judge the logical chain.

## File References
- `projects/<project_slug>/workspace/blueprint.md`
- `projects/<project_slug>/memory/research_log.jsonl`
- `projects/<project_slug>/memory/by_type/verification.md`
- `projects/<project_slug>/memory/research_log_index.sqlite`

## Output Contract
- Return the tool result, including reviewer reports, failed reviewers, aggregated errors, and the final logic verdict.
- Free-text discussion is not equivalent to the tool result.
