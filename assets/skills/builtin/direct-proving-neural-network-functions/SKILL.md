---
name: direct-proving-neural-network-functions
description: Try direct proving routes for neural-network-function subproblems using known results, multiple proof styles, and domain-specific method transfer.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: query_memory search_knowledge
metadata:
  title: Direct Proving for Neural-Network Functions
  category: builtin
  tags: research,proof,reasoning,neural-networks,functions
  skill-standard: agentskills.io/v1
---

# Direct Proving for Neural-Network Functions

## Usage Hint
- Use this skill to prove analytic, algebraic, or dynamical claims about neural-network functions directly.
- Use it when the project has a clear neural-network-function subproblem and a proof path can start from the formulas or parameter structure.

## Summary
- Attempt direct proofs for neural-network-function subproblems using available memory, known results, and multiple proof routes.
- When standard methods fail, try domain-specific concept and method transfer before abandoning the branch.

## Execution Steps
1. Follow the current research plan and selected subproblem list, and work on one subproblem at a time.
2. For the current subproblem, state the exact proposition to be proved, along with the assumptions, definitions, and subproblem context needed for the attempt.
3. Summarize the relevant theorems, formulas, laws, tools, and methods already available for this subproblem.
4. Try multiple proof routes using those ingredients, including direct proof, contradiction, induction, reduction, or adaptation of known arguments; test more than one route when needed.
5. When existing methods fail, attempt creative thinking, such as proposing new concepts or methods. By analogy with the methods and concepts related to polynomials in the background material, abstract the relevant methods and concepts for neural network functions; further refine these methods and concepts by incorporating the characteristics of neural networks to form a unique system of methods and concepts for neural networks.  
6. For neural-network-function settings, abstract useful ideas from related polynomial arguments or other background methods, then refine them so they fit the distinctive features of neural-network functions.
7. Apply the resulting methods back to the current subproblem, test whether the route is valid, and mark the attempt as solved, partial, or stuck.
8. State stable lemmas, partial conclusions, or blockers with their assumptions and evidence.

## Tool Calls
- `query_memory`: Retrieve related solve attempts, failed paths, and prior neural-network-function branches.
- `search_knowledge`: Find reusable conclusions and mathematical patterns.

## File References
- `projects/<project_slug>/memory/by_type/verified_conclusion.md`
- `projects/<project_slug>/memory/by_type/research_note.md`
- `projects/<project_slug>/memory/research_log_index.sqlite`

## Output Contract
- Return solve attempts, solved steps, stuck points, and next actions.

## Retrieval Guidance
- When a direct attempt materially advances the branch but is not yet a formal conclusion, state the claim, assumptions, evidence, and remaining gap clearly.

## Notes
- Prefer this skill over the generic direct-proving skill when the claim is specifically about neural-network functions, expressive behavior, approximation classes, or architecture-dependent function properties.
- A failed proof is useful if it clearly identifies what breaks.
