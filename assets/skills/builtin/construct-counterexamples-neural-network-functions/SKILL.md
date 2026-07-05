---
name: construct-counterexamples-neural-network-functions
description: Search for counterexamples to claims about neural-network functions by combining boundary analysis, small-example search, and cross-domain transfer.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: query_memory search_knowledge
metadata:
  title: Construct Counterexamples for Neural-Network Functions
  category: builtin
  tags: research,counterexamples,neural-networks,functions
  skill-standard: agentskills.io/v1
---

# Construct Counterexamples for Neural-Network Functions

## Usage Hint
- Use this skill to search for counterexamples in neural-network-function problems.
- Use it when a claim about sigmoid sums, parameter choices, zero sets, dynamics, or analytic structure may fail in special architectures.

## Summary
- Test claims about neural-network functions by actively constructing examples that satisfy the hypotheses but violate the conclusion.
- Use small constructions, transferred patterns, and rigorous assumption checks before treating a claim as reliable.

## Execution Steps
1. Precisely analyze the mathematical problem: clarify the hypotheses and conclusion, and identify implicit constraints, symmetries, regularity assumptions, or representation restrictions.
2. Test boundary and degenerate cases: start with extreme parameters, trivial models, high-symmetry settings, singular structures, or collapsed architectures to see whether the conclusion fails.
3. Modify known instances: start from proven theorems, known constructions, or known counterexamples, then perturb, combine, specialize, or change dimension, width, depth, or activation structure to break a key condition behind the claim.
4. Search in low dimensions or small scales: prioritize low-dimensional inputs, low-width networks, shallow architectures, small parameter families, or simple function classes where exhaustive or heuristic search is feasible.
5. Use cross-domain transfer when helpful: summarize counterexample patterns from nearby areas such as polynomial approximation, harmonic analysis, or classical function spaces, then adapt them by analogy to neural-network function problems.
6. Validate every candidate rigorously: the hypotheses must all be satisfied, and the conclusion must fail. If a counterexample is valid, state exactly which claim, branch, or formulation it refutes.

## Tool Calls
- `query_memory`: Retrieve prior counterexamples, failed paths, or related function-class comparisons.
- `search_knowledge`: Check known obstructions, examples, and reusable mathematical patterns.

## File References
- `projects/<project_slug>/memory/by_type/verified_conclusion.md`
- `projects/<project_slug>/memory/by_type/problem.md`

## Output Contract
- Return candidate counterexamples with explicit hypothesis checks, the mechanism that breaks the conclusion, and the impact on the current research plan.
- When a genuine neural-network-function counterexample is found, state it cleanly; it remains easy to retrieve and reuse later.
- Prior counterexamples can be retrieved from `research_log.jsonl` when relevant.

## Notes
- Prefer this skill over the generic counterexample skill when the claim is specifically about neural-network functions, expressive behavior, approximation classes, or architecture-dependent function properties.
- If no counterexample is found, say only that the search was inconclusive.
