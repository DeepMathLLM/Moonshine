---
name: pessimistic-verifier
description: Legacy fallback verifier that runs independent pessimistic proof audits and rejects the proof if any reviewer finds a wrong or inconclusive result; the claim sent for verification must be a complete standalone statement with all conditions and a clear conclusion.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: pessimistic_verify query_memory search_knowledge
metadata:
  title: Pessimistic Verifier
  category: builtin
  tags: research,verification,proof
  skill-standard: agentskills.io/v1
---

# Pessimistic Verifier

## Usage Hint
- Use this skill to examine a claim or proof with an adversarial correctness mindset.
- Use it when an important lemma, theorem, reduction, or final argument needs skeptical checking before being trusted.
- Before calling a verification tool, formulate the claim or conclusion as a complete standalone statement, including all relevant hypotheses, definitions, parameter restrictions, domains, and the exact conclusion to be checked.

## Summary
- This is the legacy fallback verifier. The current research workflow prefers `$verify-overall`.
- Review a proof attempt under a pessimistic rule: any serious error, unresolved gap, or inconclusive reviewer invalidates the proof.
- Focus on logical chain completeness, theorem applicability, hidden assumptions, calculations, and consistency.
- Use the executable `pessimistic_verify` tool for the actual multi-reviewer audit; this skill is not complete if it only produces free text.

## Execution Steps
1. Restate the target statement as a complete standalone claim with all assumptions, conditions, definitions, and a clear conclusion.
2. Use `query_memory` or `search_knowledge` if prior verifier reports, reusable lemmas, definitions, or assumptions are needed.
3. Call `pessimistic_verify` with the claim, proof/proof blueprint, relevant context, and project slug. Omit `review_count` unless you intentionally want to override the configured or tool default for this call.
4. Treat the aggregate as passed only when `overall_verdict` is `passed`.
5. If any reviewer reports `wrong`, `inconclusive`, a gap, hidden assumption, citation issue, or calculation issue, mark the proof as not established and route to correction.

## Tool Calls
- `pessimistic_verify`: Run independent schema-constrained LLM reviews and aggregate them with the any-failure rule.
- `query_memory`: Retrieve prior verifier reports and known gaps.
- `search_knowledge`: Check cited reusable conclusions and assumptions.

## File References
- `projects/<project_slug>/memory/by_type/verified_conclusion.md`
- `projects/<project_slug>/memory/by_type/problem.md`
- `projects/<project_slug>/memory/research_log_index.sqlite`

## Output Contract
- Return the `pessimistic_verify` aggregate result: `overall_verdict`, reviewer reports, failed reviewers, critical errors, gaps, hidden assumptions, citation issues, calculation issues, and repair hints.
- Free-text discussion alone is not an equivalent substitute for the tool result.

## Notes
- Inconclusive is not correct; it means more proof work is required.
- Before treating a result as verified, the relevant claim should have a passed pessimistic verification result.
