---
name: propose-research-plan-neural-network-functions
description: Propose materially different research plans for neural-network-function problems using prior problem understanding and special-case evidence.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: read_runtime_file query_memory search_knowledge
metadata:
  title: Propose Research Plan for Neural-Network Functions
  category: builtin
  tags: research,planning,subgoals,neural-networks,functions
  skill-standard: agentskills.io/v1
---

# Propose Research Plan for Neural-Network Functions

## Usage Hint
- Use this skill to plan a neural-network-function research attack at the level of subproblems and methods.
- Use it when a candidate problem is chosen but detailed proof search does not yet have a stable branch structure.

## Summary
- Propose materially different research plans for neural-network-function problems instead of committing too early to one decomposition.
- Build plans from prior problem understanding and special-case evidence, and make the subproblem structure explicit and checkable.

## Execution Steps
1. Read the available notes or files corresponding to problem understanding and examination of special cases before proposing plans.
2. Based on those materials, propose multiple materially different research plans rather than minor variants of a single route.
3. For each plan, break the original problem into several subproblems using the principle of moving from easier parts to harder ones and breaking the whole into manageable parts.
4. Ensure that each subproblem is independently checkable, that dependencies between subproblems are explicit, and that the decomposition avoids circular reasoning.
5. Prefer decompositions whose intermediate conclusions are general and reusable rather than overly narrow branch-specific facts.
6. Check that the decomposition covers the core difficulties of the original problem and that each subproblem remains mathematically manageable.
7. For each plan, explain why the proposed subproblem structure is rational and why the combination of the subproblems could plausibly yield a solution to the original problem.
8. Highlight the key risk, expected bottleneck, and first screening test for each plan.

## Tool Calls
- `read_runtime_file`: Read local files or notes containing prior problem-understanding summaries and special-case analysis.
- `query_memory`: Recover prior understanding notes, special cases, failed paths, and earlier planning attempts.
- `search_knowledge`: Reuse known theorem summaries, reusable lemmas, and planning-relevant mathematical patterns.

## File References
- `projects/<project_slug>/workspace/problem.md`
- `projects/<project_slug>/memory/research_log.jsonl`
- `projects/<project_slug>/memory/by_type/research_note.md`
- `projects/<project_slug>/references/`

## Output Contract
- Return multiple materially different research plans.
- For each plan, include:
  - the ordered subproblem decomposition
  - dependency structure
  - why the decomposition is rational
  - why the combined subproblems could yield the original solution
  - the main bottleneck or risk
- If one plan becomes the working plan, state it clearly.
- If no viable plan survives repeated attempts, give a concise failed-planning summary.

## Notes
- Prefer this skill over the generic decomposition skill when the problem is specifically about neural-network functions and prior understanding or special-case analysis already exists.
- Do not hide a hard part inside a vague subproblem.
