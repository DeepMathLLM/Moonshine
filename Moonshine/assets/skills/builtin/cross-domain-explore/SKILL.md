---
name: cross-domain-explore
description: Look for useful analogies and methods from nearby mathematical areas.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: query_memory search_knowledge
metadata:
  title: Cross Domain Explore
  category: builtin
  tags: research,analogy,exploration
  skill-standard: agentskills.io/v1
---

# Cross Domain Explore

## Usage Hint
- Use this skill to import useful analogies, tools, or theorem patterns from adjacent mathematical areas.
- Use it when direct methods stall or the current structure resembles a known problem in another domain.

## Summary
- Search for neighboring fields, analogous statements, and transferable proof patterns.
- Use this when a direct approach stalls or when designing new research questions.

## Execution Steps
1. State the current target problem and its mathematical structure.
2. List adjacent areas where similar structures appear.
3. Compare assumptions, invariants, obstructions, and proof tools.
4. Propose candidate analogies and explain what might or might not transfer.
5. Record promising directions and risks.

## Tool Calls
- `query_memory`: Recover prior analogies, failed transfers, and cross-project insights.
- `search_knowledge`: Find stored conclusions that may transfer across domains.

## File References
- `projects/<project_slug>/memory/research_log.jsonl`
- `projects/<project_slug>/memory/by_type/problem.md`
- `projects/<project_slug>/memory/research_log_index.sqlite`

## Output Contract
- Return a short list of cross-domain hypotheses, each with transfer rationale and failure risk.

## Notes
- Analogies are evidence generators, not proofs.
