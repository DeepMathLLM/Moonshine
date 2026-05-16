---
name: lemma-prover
description: Prove, refute, or refine a candidate lemma needed by the main research problem.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: query_memory search_knowledge verify_overall verify_correctness_assumption verify_correctness_computation verify_correctness_logic
metadata:
  title: Lemma Prover
  category: builtin
  tags: research,lemma,proof
  skill-standard: agentskills.io/v1
---

# Lemma Prover

## Usage Hint
- Use this skill to prove a focused lemma needed by a larger argument.
- Use it when the main theorem depends on a precise subclaim with fixed hypotheses.

## Summary
- Focus on one candidate lemma and determine whether it is proven, partial, refuted, or needs refinement.
- Preserve assumptions and proof dependencies carefully.

## Execution Steps
1. Restate the lemma with all hypotheses.
2. Check whether it follows from reusable knowledge or prior work.
3. Attempt a proof or look for counterexamples.
4. If proven or partially proven, state the result with evidence.
5. If refuted, state the counterexample and affected branch.

## Tool Calls
- `query_memory`: Retrieve prior lemma attempts and examples.
- `search_knowledge`: Check related reusable conclusions.
- `verify_overall`: Verify an important lemma before treating it as established.
- `verify_correctness_assumption`, `verify_correctness_computation`, `verify_correctness_logic`: Use only when one verification dimension needs targeted diagnosis.

## File References
- `projects/<project_slug>/memory/by_type/verified_conclusion.md`
- `projects/<project_slug>/memory/by_type/problem.md`
- `projects/<project_slug>/memory/research_log_index.sqlite`

## Output Contract
- Return lemma status, proof sketch or counterexample, dependencies, and implications.
- If the lemma is important and plausibly complete, run `verify_overall`.
- If the lemma remains partial, refuted, or in need of reformulation, state that status explicitly.
- Prior lemma progress can be retrieved from `research_log.jsonl` when relevant.

## Notes
- Do not promote a lemma to verified unless the proof is complete.
