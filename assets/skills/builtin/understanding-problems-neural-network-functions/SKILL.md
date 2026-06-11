---
name: understanding-problems-neural-network-functions
description: Build a programmatic understanding of neural-network-function problems by combining the given problem statement, polynomial background material, and structural analogy.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: read_runtime_file query_memory search_knowledge
metadata:
  title: Understanding Problems for Neural-Network Functions
  category: builtin
  tags: research,problem-understanding,neural-networks,functions
  skill-standard: agentskills.io/v1
---

# Understanding Problems for Neural-Network Functions

## Usage Hint
- Use this skill to clarify a neural-network-function problem before choosing a proof strategy.
- Use it when the statement, parameter regime, analytic setting, or intended conclusion is not yet fully understood.

## Summary
- Build a programmatic understanding of neural-network-function problems before trying to prove, refute, or refine them.
- Focus on the connotation and extension of the problem, together with the knowledge points and methods that may matter, rather than giving detailed proofs.

## Execution Steps
1. Read the mathematical problems about neural-network functions from the available local files.
2. Read the available local background material on polynomials.
3. Extract the relevant knowledge points, theorem patterns, and research methods from the polynomial background material.
4. Use structural analogy to map those polynomial knowledge points and methods into the neural-network-function setting, identifying which concepts and methods plausibly transfer.
5. Check whether the transferred knowledge points and methods are actually reasonable in the neural-network setting; then temporarily set polynomials aside and think directly from the intrinsic features of neural networks themselves.
6. Refine the final list of neural-network-relevant knowledge points, possible methods, conceptual obstructions, and natural research directions.
7. Keep the result at the level of problem understanding and research planning; do not spend the turn on detailed proofs unless the task explicitly changes.

## Tool Calls
- `read_runtime_file`: Read the local neural-network-function problem statements and the polynomial background material.
- `query_memory`: Retrieve prior project understanding notes, failed directions, or already-identified knowledge gaps.
- `search_knowledge`: Reuse known theorem summaries, method notes, and related mathematical conclusions.

## File References
- `projects/<project_slug>/workspace/problem.md`
- `projects/<project_slug>/memory/research_log.jsonl`
- `projects/<project_slug>/memory/by_type/research_note.md`
- `projects/<project_slug>/references/`
- `projects/<project_slug>/references/surveys/`

## Output Contract
- Return a structured natural-language understanding report, not a detailed proof.
- The report should clarify: the meaning of the problem, relevant knowledge points, plausible methods, natural analogies, and which ideas appear transferable or questionable.
- When the resulting understanding should survive later turns, state it clearly enough that later work can reuse it directly from prior context if needed.
- If repeated attempts reveal that the analogy or background transfer is misleading, state that failure clearly instead of forcing a weak interpretation.

## Notes
- This skill is for understanding and framing the problem, not for proving the final result.
- If no durable understanding note or failure summary is needed, no persistence action is required.
