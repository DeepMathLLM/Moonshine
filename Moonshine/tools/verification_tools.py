"""Pessimistic proof-verification tools for Moonshine."""

from __future__ import annotations

from typing import Dict, List, Tuple

from moonshine.json_schema import validate_json_schema
from moonshine.providers import OfflineProvider
from moonshine.structured_tasks import register_structured_task
from moonshine.utils import shorten, utc_now


PESSIMISTIC_REVIEW_SCHEMA: Dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "reviewer_id": {"type": "string", "minLength": 1},
        "review_focus": {"type": "string", "minLength": 1},
        "verdict": {"type": "string", "enum": ["correct", "wrong", "inconclusive"]},
        "logical_chain_complete": {"type": "boolean"},
        "theorem_use_valid": {"type": "boolean"},
        "assumptions_explicit": {"type": "boolean"},
        "calculations_valid": {"type": "boolean"},
        "premise_conclusion_match": {"type": "boolean"},
        "critical_errors": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "hidden_assumptions": {"type": "array", "items": {"type": "string"}},
        "citation_issues": {"type": "array", "items": {"type": "string"}},
        "calculation_issues": {"type": "array", "items": {"type": "string"}},
        "repair_hints": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": [
        "reviewer_id",
        "review_focus",
        "verdict",
        "logical_chain_complete",
        "theorem_use_valid",
        "assumptions_explicit",
        "calculations_valid",
        "premise_conclusion_match",
        "critical_errors",
        "gaps",
        "hidden_assumptions",
        "citation_issues",
        "calculation_issues",
        "repair_hints",
        "rationale",
        "confidence",
    ],
}


PESSIMISTIC_VERIFICATION_RESULT_SCHEMA: Dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "tool": {"type": "string", "enum": ["pessimistic_verify"]},
        "status": {"type": "string", "enum": ["completed"]},
        "passed": {"type": "boolean"},
        "overall_verdict": {"type": "string", "enum": ["passed", "failed"]},
        "failure_policy": {"type": "string"},
        "review_count": {"type": "integer"},
        "failed_reviewers": {"type": "array", "items": {"type": "string"}},
        "claim": {"type": "string"},
        "project_slug": {"type": "string"},
        "reviewed_at": {"type": "string"},
        "reviews": {"type": "array", "items": PESSIMISTIC_REVIEW_SCHEMA},
        "critical_errors": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "hidden_assumptions": {"type": "array", "items": {"type": "string"}},
        "citation_issues": {"type": "array", "items": {"type": "string"}},
        "calculation_issues": {"type": "array", "items": {"type": "string"}},
        "repair_hints": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": [
        "tool",
        "status",
        "passed",
        "overall_verdict",
        "failure_policy",
        "review_count",
        "failed_reviewers",
        "claim",
        "project_slug",
        "reviewed_at",
        "reviews",
        "critical_errors",
        "gaps",
        "hidden_assumptions",
        "citation_issues",
        "calculation_issues",
        "repair_hints",
        "summary",
    ],
}


DIMENSION_REVIEW_SCHEMA: Dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "reviewer_id": {"type": "string", "minLength": 1},
        "dimension": {"type": "string", "enum": ["assumption", "computation", "logic"]},
        "review_focus": {"type": "string", "minLength": 1},
        "verdict": {"type": "string", "enum": ["correct", "incorrect", "inconclusive"]},
        "error_count": {"type": "integer", "minimum": 0},
        "errors": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": [
        "reviewer_id",
        "dimension",
        "review_focus",
        "verdict",
        "error_count",
        "errors",
        "rationale",
        "confidence",
    ],
}


DIMENSION_RESULT_SCHEMA: Dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "tool": {
            "type": "string",
            "enum": [
                "verify_correctness_assumption",
                "verify_correctness_computation",
                "verify_correctness_logic",
            ],
        },
        "dimension": {"type": "string", "enum": ["assumption", "computation", "logic"]},
        "status": {"type": "string", "enum": ["completed"]},
        "passed": {"type": "boolean"},
        "overall_verdict": {
            "type": "string",
            "enum": [
                "assumption_correct",
                "assumption_incorrect",
                "calculation_correct",
                "calculation_incorrect",
                "logic_correct",
                "logic_incorrect",
            ],
        },
        "failure_policy": {"type": "string"},
        "review_count": {"type": "integer"},
        "failed_reviewers": {"type": "array", "items": {"type": "string"}},
        "claim": {"type": "string"},
        "project_slug": {"type": "string"},
        "reviewed_at": {"type": "string"},
        "scope": {"type": "string"},
        "blueprint_path": {"type": "string"},
        "reviews": {"type": "array", "items": DIMENSION_REVIEW_SCHEMA},
        "errors": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": [
        "tool",
        "dimension",
        "status",
        "passed",
        "overall_verdict",
        "failure_policy",
        "review_count",
        "failed_reviewers",
        "claim",
        "project_slug",
        "reviewed_at",
        "scope",
        "blueprint_path",
        "reviews",
        "errors",
        "summary",
    ],
}


OVERALL_VERIFICATION_RESULT_SCHEMA: Dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "tool": {"type": "string", "enum": ["verify_overall"]},
        "status": {"type": "string", "enum": ["completed"]},
        "passed": {"type": "boolean"},
        "overall_verdict": {"type": "string", "enum": ["correct", "incorrect"]},
        "failure_policy": {"type": "string"},
        "claim": {"type": "string"},
        "project_slug": {"type": "string"},
        "reviewed_at": {"type": "string"},
        "scope": {"type": "string"},
        "blueprint_path": {"type": "string"},
        "assumption_result": DIMENSION_RESULT_SCHEMA,
        "computation_result": DIMENSION_RESULT_SCHEMA,
        "logic_result": DIMENSION_RESULT_SCHEMA,
        "repair_targets": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": [
        "tool",
        "status",
        "passed",
        "overall_verdict",
        "failure_policy",
        "claim",
        "project_slug",
        "reviewed_at",
        "scope",
        "blueprint_path",
        "assumption_result",
        "computation_result",
        "logic_result",
        "repair_targets",
        "summary",
    ],
}


register_structured_task(
    task_name="pessimistic-verification-review",
    schema_name="pessimistic_verification_review",
    schema=PESSIMISTIC_REVIEW_SCHEMA,
    description="One independent schema-constrained proof review for the pessimistic verifier.",
)

register_structured_task(
    task_name="pessimistic-verification-result",
    schema_name="pessimistic_verification_result",
    schema=PESSIMISTIC_VERIFICATION_RESULT_SCHEMA,
    description="Aggregated pessimistic-verifier result where any non-correct review fails.",
)

register_structured_task(
    task_name="verify-correctness-dimension-review",
    schema_name="verify_correctness_dimension_review",
    schema=DIMENSION_REVIEW_SCHEMA,
    description="One independent schema-constrained review for a single verification dimension.",
)

register_structured_task(
    task_name="verify-correctness-dimension-result",
    schema_name="verify_correctness_dimension_result",
    schema=DIMENSION_RESULT_SCHEMA,
    description="Aggregated result for one correctness dimension using pessimistic any-reviewer failure semantics.",
)

register_structured_task(
    task_name="verify-overall-result",
    schema_name="verify_overall_result",
    schema=OVERALL_VERIFICATION_RESULT_SCHEMA,
    description="Aggregated multidimensional verification result that passes only when assumption, computation, and logic all pass.",
)


REVIEWER_PROFILES: List[Tuple[str, str]] = [
    (
        "logic-chain-reviewer",
        "Audit whether every implication in the proof follows from stated assumptions, definitions, or cited results.",
    ),
    (
        "theorem-and-assumption-reviewer",
        "Audit theorem applicability, citation compatibility, hidden assumptions, and premise/conclusion alignment.",
    ),
    (
        "calculation-and-edge-case-reviewer",
        "Audit computations, algebraic transformations, edge cases, examples, and possible counterexamples.",
    ),
    (
        "adversarial-gap-reviewer",
        "Try to refute the proof by finding the most damaging missing lemma, unproved transition, or invalid reduction.",
    ),
    (
        "integration-reviewer",
        "Audit the whole proof blueprint for dependency order, circularity, and whether the final conclusion really follows.",
    ),
]


DIMENSION_REVIEWER_PROFILES: Dict[str, List[Tuple[str, str]]] = {
    "assumption": [
        (
            "assumption-usage-reviewer",
            "Check whether every stated condition and assumption is actually used somewhere in the solution process.",
        ),
        (
            "premise-coverage-reviewer",
            "Check for missing or idle assumptions and verify that the proof never silently relies on unstated premises.",
        ),
        (
            "hypothesis-tracking-reviewer",
            "Track hypothesis use step by step and report every place where an assumption is skipped, unused, or mismatched.",
        ),
    ],
    "computation": [
        (
            "calculation-consistency-reviewer",
            "Check algebraic, analytic, and symbolic computations only; ignore non-computational issues.",
        ),
        (
            "arithmetic-and-transform-reviewer",
            "Check arithmetic steps, transformations, substitutions, and simplifications only; ignore other issues.",
        ),
        (
            "computational-edge-reviewer",
            "Check calculations and manipulations under all explicit steps and edge substitutions only; ignore other issues.",
        ),
    ],
    "logic": [
        (
            "logical-chain-reviewer",
            "Check logical implication steps and proof structure only; ignore purely computational issues.",
        ),
        (
            "gap-and-circularity-reviewer",
            "Check for logical gaps, circular reasoning, unjustified transitions, and missing deductions only.",
        ),
        (
            "premise-conclusion-reviewer",
            "Check that the conclusion truly follows from the proven steps and assumptions; ignore computation unless it creates a logical flaw.",
        ),
    ],
}


DIMENSION_SPECS: Dict[str, Dict[str, str]] = {
    "assumption": {
        "tool": "verify_correctness_assumption",
        "label": "assumption",
        "pass_verdict": "assumption_correct",
        "fail_verdict": "assumption_incorrect",
        "focus_rule": "Only check whether every condition and assumption involved in the subproblem has been used. Do not check other errors.",
    },
    "computation": {
        "tool": "verify_correctness_computation",
        "label": "calculation",
        "pass_verdict": "calculation_correct",
        "fail_verdict": "calculation_incorrect",
        "focus_rule": "Only check for calculation errors. Do not check logical or assumption-use errors.",
    },
    "logic": {
        "tool": "verify_correctness_logic",
        "label": "logic",
        "pass_verdict": "logic_correct",
        "fail_verdict": "logic_incorrect",
        "focus_rule": "Only check for logical errors and flaws. Do not check other kinds of errors unless they create a logical flaw.",
    },
}


def _bounded_review_count(review_count: int) -> int:
    """Keep verification useful without letting one tool call consume the whole run."""
    try:
        value = int(review_count)
    except (TypeError, ValueError):
        value = 3
    return max(1, min(value, len(REVIEWER_PROFILES)))


def _dimension_review_count(runtime: dict, explicit_count: int = 0) -> int:
    """Resolve the configured reviewer count for one verification dimension."""
    try:
        value = int(explicit_count or 0)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        config = runtime.get("config") if isinstance(runtime, dict) else None
        agent_config = getattr(config, "agent", None)
        try:
            value = int(getattr(agent_config, "verification_dimension_review_count", 1))
        except (TypeError, ValueError):
            value = 1
    return max(1, min(value, max(len(items) for items in DIMENSION_REVIEWER_PROFILES.values())))


def _failure_review(*, reviewer_id: str, review_focus: str, reason: str) -> Dict[str, object]:
    """Return a schema-valid conservative failure review."""
    return {
        "reviewer_id": reviewer_id,
        "review_focus": review_focus,
        "verdict": "inconclusive",
        "logical_chain_complete": False,
        "theorem_use_valid": False,
        "assumptions_explicit": False,
        "calculations_valid": False,
        "premise_conclusion_match": False,
        "critical_errors": [],
        "gaps": [shorten(reason, 1500)],
        "hidden_assumptions": [],
        "citation_issues": [],
        "calculation_issues": [],
        "repair_hints": ["Run the verifier again with an available structured LLM reviewer."],
        "rationale": shorten(reason, 2200),
        "confidence": 0.0,
    }


def _failure_dimension_review(*, reviewer_id: str, dimension: str, review_focus: str, reason: str) -> Dict[str, object]:
    """Return a conservative schema-valid failure review for one single dimension."""
    return {
        "reviewer_id": reviewer_id,
        "dimension": dimension,
        "review_focus": review_focus,
        "verdict": "inconclusive",
        "error_count": 1,
        "errors": [shorten(reason, 1500)],
        "rationale": shorten(reason, 2200),
        "confidence": 0.0,
    }


def _normal_review_payload(payload: Dict[str, object], *, reviewer_id: str, review_focus: str) -> Dict[str, object]:
    """Normalize and validate one reviewer payload."""
    review = dict(payload or {})
    review["reviewer_id"] = str(review.get("reviewer_id") or reviewer_id)
    review["review_focus"] = str(review.get("review_focus") or review_focus)
    for key in [
        "critical_errors",
        "gaps",
        "hidden_assumptions",
        "citation_issues",
        "calculation_issues",
        "repair_hints",
    ]:
        value = review.get(key)
        if not isinstance(value, list):
            review[key] = [] if value in (None, "") else [str(value)]
        else:
            review[key] = [str(item) for item in value]
    for key in [
        "logical_chain_complete",
        "theorem_use_valid",
        "assumptions_explicit",
        "calculations_valid",
        "premise_conclusion_match",
    ]:
        if not isinstance(review.get(key), bool):
            review[key] = False
    if review.get("verdict") not in {"correct", "wrong", "inconclusive"}:
        review["verdict"] = "inconclusive"
    if not isinstance(review.get("confidence"), (int, float)) or isinstance(review.get("confidence"), bool):
        review["confidence"] = 0.0
    review["rationale"] = str(review.get("rationale") or "")
    validate_json_schema(review, PESSIMISTIC_REVIEW_SCHEMA)
    return review


def _normal_dimension_review_payload(
    payload: Dict[str, object],
    *,
    reviewer_id: str,
    dimension: str,
    review_focus: str,
) -> Dict[str, object]:
    """Normalize and validate one single-dimension reviewer payload."""
    review = dict(payload or {})
    review["reviewer_id"] = str(review.get("reviewer_id") or reviewer_id)
    review["dimension"] = str(review.get("dimension") or dimension)
    review["review_focus"] = str(review.get("review_focus") or review_focus)
    value = review.get("errors")
    if not isinstance(value, list):
        review["errors"] = [] if value in (None, "") else [str(value)]
    else:
        review["errors"] = [str(item) for item in value]
    if review.get("verdict") not in {"correct", "incorrect", "inconclusive"}:
        review["verdict"] = "inconclusive"
    try:
        review["error_count"] = max(0, int(review.get("error_count", len(review["errors"])) or 0))
    except (TypeError, ValueError):
        review["error_count"] = len(review["errors"])
    if review["error_count"] <= 0 and review["errors"]:
        review["error_count"] = len(review["errors"])
    if not isinstance(review.get("confidence"), (int, float)) or isinstance(review.get("confidence"), bool):
        review["confidence"] = 0.0
    review["rationale"] = str(review.get("rationale") or "")
    validate_json_schema(review, DIMENSION_REVIEW_SCHEMA)
    return review


def _provider_available(provider) -> bool:
    """Return True when the current provider can make structured review calls."""
    return provider is not None and not isinstance(provider, OfflineProvider) and hasattr(provider, "generate_structured")


def _review_prompt(*, claim: str, proof: str, context: str, reviewer_id: str, review_focus: str) -> str:
    """Build an isolated reviewer prompt."""
    return (
        "You are one independent reviewer in Moonshine's pessimistic verification protocol.\n"
        "Do not assume other reviewers will catch errors. Be adversarial and precise.\n"
        "Verdict policy:\n"
        "- Return correct only if the proof is complete and the focus area has no serious issue.\n"
        "- Return wrong if you find a fatal error, invalid citation, counterexample, or contradiction.\n"
        "- Return inconclusive if the proof lacks enough detail to establish correctness.\n\n"
        "Reviewer ID: {reviewer_id}\n"
        "Reviewer focus: {review_focus}\n\n"
        "Target claim:\n{claim}\n\n"
        "Proof or proof blueprint to audit:\n{proof}\n\n"
        "Additional context:\n{context}"
    ).format(
        reviewer_id=reviewer_id,
        review_focus=review_focus,
        claim=claim,
        proof=proof,
        context=context or "(none)",
    )


def _dimension_review_prompt(
    *,
    claim: str,
    proof: str,
    context: str,
    reviewer_id: str,
    review_focus: str,
    dimension: str,
) -> str:
    """Build an isolated one-dimension reviewer prompt."""
    focus_rule = DIMENSION_SPECS[dimension]["focus_rule"]
    return (
        "You are one independent reviewer in Moonshine's multidimensional correctness verification protocol.\n"
        "Check exactly one dimension and ignore the others.\n"
        "Review the detailed solution process from beginning to end. If you find an error, record it and continue auditing the remaining steps.\n"
        "Verdict policy:\n"
        "- Return correct only if this dimension has no error at all.\n"
        "- Return incorrect if you find one or more errors in this dimension.\n"
        "- Return inconclusive if the proof lacks enough detail to establish correctness even for this single dimension.\n\n"
        "Reviewer ID: {reviewer_id}\n"
        "Dimension: {dimension}\n"
        "Reviewer focus: {review_focus}\n"
        "Dimension rule: {focus_rule}\n\n"
        "Target claim:\n{claim}\n\n"
        "Solution process or proof blueprint to audit:\n{proof}\n\n"
        "Additional context:\n{context}"
    ).format(
        reviewer_id=reviewer_id,
        dimension=dimension,
        review_focus=review_focus,
        focus_rule=focus_rule,
        claim=claim,
        proof=proof,
        context=context or "(none)",
    )


def _run_one_review(
    provider,
    *,
    claim: str,
    proof: str,
    context: str,
    reviewer_id: str,
    review_focus: str,
) -> Dict[str, object]:
    """Run one independent structured review, falling back conservatively on errors."""
    if not _provider_available(provider):
        return _failure_review(
            reviewer_id=reviewer_id,
            review_focus=review_focus,
            reason="No structured LLM provider is available for independent pessimistic verification.",
        )
    try:
        payload = provider.generate_structured(
            system_prompt=(
                "You are an independent mathematical proof reviewer. "
                "You must return only a JSON object matching the supplied schema. "
                "Be pessimistic: incomplete proof detail is inconclusive, not correct."
            ),
            messages=[
                {
                    "role": "user",
                    "content": _review_prompt(
                        claim=claim,
                        proof=proof,
                        context=context,
                        reviewer_id=reviewer_id,
                        review_focus=review_focus,
                    ),
                }
            ],
            response_schema=PESSIMISTIC_REVIEW_SCHEMA,
            schema_name="pessimistic_verification_review",
        )
        return _normal_review_payload(dict(payload), reviewer_id=reviewer_id, review_focus=review_focus)
    except Exception as exc:
        return _failure_review(
            reviewer_id=reviewer_id,
            review_focus=review_focus,
            reason="Structured reviewer failed or returned invalid output: %s" % exc,
        )


def _run_one_dimension_review(
    provider,
    *,
    claim: str,
    proof: str,
    context: str,
    reviewer_id: str,
    review_focus: str,
    dimension: str,
) -> Dict[str, object]:
    """Run one independent structured review for a single verification dimension."""
    if not _provider_available(provider):
        return _failure_dimension_review(
            reviewer_id=reviewer_id,
            dimension=dimension,
            review_focus=review_focus,
            reason="No structured LLM provider is available for independent multidimensional verification.",
        )
    try:
        payload = provider.generate_structured(
            system_prompt=(
                "You are an independent mathematical reviewer. "
                "You must return only a JSON object matching the supplied schema. "
                "Check only the assigned dimension and be pessimistic: incomplete detail is inconclusive."
            ),
            messages=[
                {
                    "role": "user",
                    "content": _dimension_review_prompt(
                        claim=claim,
                        proof=proof,
                        context=context,
                        reviewer_id=reviewer_id,
                        review_focus=review_focus,
                        dimension=dimension,
                    ),
                }
            ],
            response_schema=DIMENSION_REVIEW_SCHEMA,
            schema_name="verify_correctness_dimension_review",
        )
        return _normal_dimension_review_payload(
            dict(payload),
            reviewer_id=reviewer_id,
            dimension=dimension,
            review_focus=review_focus,
        )
    except Exception as exc:
        return _failure_dimension_review(
            reviewer_id=reviewer_id,
            dimension=dimension,
            review_focus=review_focus,
            reason="Structured reviewer failed or returned invalid output: %s" % exc,
        )


def _dedupe(items: List[object]) -> List[str]:
    """Deduplicate strings while preserving order."""
    seen = set()
    result = []
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _review_failed(review: Dict[str, object]) -> bool:
    """Apply Moonshine's pessimistic aggregate policy to one review."""
    if review.get("verdict") != "correct":
        return True
    if any(review.get(key) for key in ["critical_errors", "gaps", "hidden_assumptions", "citation_issues", "calculation_issues"]):
        return True
    for key in [
        "logical_chain_complete",
        "theorem_use_valid",
        "assumptions_explicit",
        "calculations_valid",
        "premise_conclusion_match",
    ]:
        if review.get(key) is not True:
            return True
    return False


def _dimension_review_failed(review: Dict[str, object]) -> bool:
    """Apply pessimistic any-error semantics to one dimension review."""
    if review.get("verdict") != "correct":
        return True
    if int(review.get("error_count", 0) or 0) > 0:
        return True
    if list(review.get("errors") or []):
        return True
    return False


def _aggregate_reviews(*, claim: str, project_slug: str, reviews: List[Dict[str, object]]) -> Dict[str, object]:
    """Aggregate independent reviews with the any-failure rule."""
    failed_reviewers = [str(review["reviewer_id"]) for review in reviews if _review_failed(review)]
    passed = not failed_reviewers
    critical_errors = _dedupe([item for review in reviews for item in list(review.get("critical_errors") or [])])
    gaps = _dedupe([item for review in reviews for item in list(review.get("gaps") or [])])
    hidden_assumptions = _dedupe([item for review in reviews for item in list(review.get("hidden_assumptions") or [])])
    citation_issues = _dedupe([item for review in reviews for item in list(review.get("citation_issues") or [])])
    calculation_issues = _dedupe([item for review in reviews for item in list(review.get("calculation_issues") or [])])
    repair_hints = _dedupe([item for review in reviews for item in list(review.get("repair_hints") or [])])
    summary = (
        "Pessimistic verification passed: all independent reviewers returned correct with no unresolved issues."
        if passed
        else "Pessimistic verification failed: %s reviewer(s) found errors, gaps, or inconclusive evidence."
        % len(failed_reviewers)
    )
    result = {
        "tool": "pessimistic_verify",
        "status": "completed",
        "passed": passed,
        "overall_verdict": "passed" if passed else "failed",
        "failure_policy": "Any reviewer with verdict other than correct, or any unresolved issue field, fails the aggregate.",
        "review_count": len(reviews),
        "failed_reviewers": failed_reviewers,
        "claim": shorten(claim, 5900),
        "project_slug": project_slug,
        "reviewed_at": utc_now(),
        "reviews": reviews,
        "critical_errors": critical_errors,
        "gaps": gaps,
        "hidden_assumptions": hidden_assumptions,
        "citation_issues": citation_issues,
        "calculation_issues": calculation_issues,
        "repair_hints": repair_hints,
        "summary": summary,
    }
    validate_json_schema(result, PESSIMISTIC_VERIFICATION_RESULT_SCHEMA)
    return result


def _aggregate_dimension_reviews(
    *,
    dimension: str,
    claim: str,
    project_slug: str,
    reviews: List[Dict[str, object]],
    scope: str,
    blueprint_path: str,
) -> Dict[str, object]:
    """Aggregate one correctness dimension using the any-non-correct-is-failure rule."""
    spec = DIMENSION_SPECS[dimension]
    failed_reviewers = [str(review["reviewer_id"]) for review in reviews if _dimension_review_failed(review)]
    passed = not failed_reviewers
    errors = _dedupe([item for review in reviews for item in list(review.get("errors") or [])])
    label = spec["label"]
    result = {
        "tool": spec["tool"],
        "dimension": dimension,
        "status": "completed",
        "passed": passed,
        "overall_verdict": spec["pass_verdict"] if passed else spec["fail_verdict"],
        "failure_policy": "Run the configured independent reviewer count for this dimension; any reviewer that is not correct, or any recorded error, fails the dimension.",
        "review_count": len(reviews),
        "failed_reviewers": failed_reviewers,
        "claim": shorten(claim, 5900),
        "project_slug": project_slug,
        "reviewed_at": utc_now(),
        "scope": str(scope or "intermediate"),
        "blueprint_path": str(blueprint_path or ""),
        "reviews": reviews,
        "errors": errors,
        "summary": (
            "The %s check passed: all independent reviewers found no %s errors."
            % (label, label)
            if passed
            else "The %s check failed: at least one reviewer found a %s issue or could not confirm correctness."
            % (label, label)
        ),
    }
    validate_json_schema(result, DIMENSION_RESULT_SCHEMA)
    return result


def _collect_dimension_reviews(
    provider,
    *,
    claim: str,
    proof: str,
    context: str,
    dimension: str,
    review_count: int = 1,
) -> List[Dict[str, object]]:
    """Run bounded independent reviews for one correctness dimension."""
    reviews = []
    count = max(1, min(int(review_count or 1), len(DIMENSION_REVIEWER_PROFILES[dimension])))
    for reviewer_id, review_focus in DIMENSION_REVIEWER_PROFILES[dimension][:count]:
        reviews.append(
            _run_one_dimension_review(
                provider,
                claim=claim,
                proof=proof,
                context=context,
                reviewer_id=reviewer_id,
                review_focus=review_focus,
                dimension=dimension,
            )
        )
    return reviews


def pessimistic_verify(
    runtime: dict,
    claim: str,
    proof: str,
    context: str = "",
    project_slug: str = "",
    review_count: int = 3,
    scope: str = "intermediate",
    blueprint_path: str = "",
) -> Dict[str, object]:
    """Run independent LLM reviews and fail if any reviewer objects."""
    resolved_project = str(project_slug or runtime.get("project_slug") or "general")
    provider = runtime.get("provider")
    count = _bounded_review_count(review_count)
    reviews = []
    for reviewer_id, review_focus in REVIEWER_PROFILES[:count]:
        reviews.append(
            _run_one_review(
                provider,
                claim=str(claim),
                proof=str(proof),
                context=str(context or ""),
                reviewer_id=reviewer_id,
                review_focus=review_focus,
            )
        )
    result = _aggregate_reviews(claim=str(claim), project_slug=resolved_project, reviews=reviews)
    result["scope"] = str(scope or "intermediate")
    result["blueprint_path"] = str(blueprint_path or "")
    return result


def verify_correctness_assumption(
    runtime: dict,
    claim: str,
    proof: str,
    context: str = "",
    project_slug: str = "",
    scope: str = "intermediate",
    blueprint_path: str = "",
    review_count: int = 0,
) -> Dict[str, object]:
    """Run the assumption-usage dimension of the multidimensional verifier."""
    resolved_project = str(project_slug or runtime.get("project_slug") or "general")
    provider = runtime.get("provider") if bool(runtime.get("verification_provider_inherit_from_main", True)) else (runtime.get("verification_provider") or runtime.get("provider"))
    count = _dimension_review_count(runtime, review_count)
    reviews = _collect_dimension_reviews(
        provider,
        claim=str(claim),
        proof=str(proof),
        context=str(context or ""),
        dimension="assumption",
        review_count=count,
    )
    return _aggregate_dimension_reviews(
        dimension="assumption",
        claim=str(claim),
        project_slug=resolved_project,
        reviews=reviews,
        scope=str(scope or "intermediate"),
        blueprint_path=str(blueprint_path or ""),
    )


def verify_correctness_computation(
    runtime: dict,
    claim: str,
    proof: str,
    context: str = "",
    project_slug: str = "",
    scope: str = "intermediate",
    blueprint_path: str = "",
    review_count: int = 0,
) -> Dict[str, object]:
    """Run the calculation-error dimension of the multidimensional verifier."""
    resolved_project = str(project_slug or runtime.get("project_slug") or "general")
    provider = runtime.get("provider") if bool(runtime.get("verification_provider_inherit_from_main", True)) else (runtime.get("verification_provider") or runtime.get("provider"))
    count = _dimension_review_count(runtime, review_count)
    reviews = _collect_dimension_reviews(
        provider,
        claim=str(claim),
        proof=str(proof),
        context=str(context or ""),
        dimension="computation",
        review_count=count,
    )
    return _aggregate_dimension_reviews(
        dimension="computation",
        claim=str(claim),
        project_slug=resolved_project,
        reviews=reviews,
        scope=str(scope or "intermediate"),
        blueprint_path=str(blueprint_path or ""),
    )


def verify_correctness_logic(
    runtime: dict,
    claim: str,
    proof: str,
    context: str = "",
    project_slug: str = "",
    scope: str = "intermediate",
    blueprint_path: str = "",
    review_count: int = 0,
) -> Dict[str, object]:
    """Run the logical-flaw dimension of the multidimensional verifier."""
    resolved_project = str(project_slug or runtime.get("project_slug") or "general")
    provider = runtime.get("provider") if bool(runtime.get("verification_provider_inherit_from_main", True)) else (runtime.get("verification_provider") or runtime.get("provider"))
    count = _dimension_review_count(runtime, review_count)
    reviews = _collect_dimension_reviews(
        provider,
        claim=str(claim),
        proof=str(proof),
        context=str(context or ""),
        dimension="logic",
        review_count=count,
    )
    return _aggregate_dimension_reviews(
        dimension="logic",
        claim=str(claim),
        project_slug=resolved_project,
        reviews=reviews,
        scope=str(scope or "intermediate"),
        blueprint_path=str(blueprint_path or ""),
    )


def verify_overall(
    runtime: dict,
    claim: str,
    proof: str,
    context: str = "",
    project_slug: str = "",
    scope: str = "intermediate",
    blueprint_path: str = "",
    review_count: int = 0,
) -> Dict[str, object]:
    """Run all three correctness dimensions and pass only if all pass."""
    resolved_project = str(project_slug or runtime.get("project_slug") or "general")
    resolved_scope = str(scope or "intermediate")
    resolved_blueprint = str(blueprint_path or "")
    count = _dimension_review_count(runtime, review_count)
    assumption_result = verify_correctness_assumption(
        runtime,
        claim=str(claim),
        proof=str(proof),
        context=str(context or ""),
        project_slug=resolved_project,
        scope=resolved_scope,
        blueprint_path=resolved_blueprint,
        review_count=count,
    )
    computation_result = verify_correctness_computation(
        runtime,
        claim=str(claim),
        proof=str(proof),
        context=str(context or ""),
        project_slug=resolved_project,
        scope=resolved_scope,
        blueprint_path=resolved_blueprint,
        review_count=count,
    )
    logic_result = verify_correctness_logic(
        runtime,
        claim=str(claim),
        proof=str(proof),
        context=str(context or ""),
        project_slug=resolved_project,
        scope=resolved_scope,
        blueprint_path=resolved_blueprint,
        review_count=count,
    )
    passed = bool(assumption_result.get("passed")) and bool(computation_result.get("passed")) and bool(logic_result.get("passed"))
    repair_targets = _dedupe(
        list(assumption_result.get("errors") or [])
        + list(computation_result.get("errors") or [])
        + list(logic_result.get("errors") or [])
    )
    if not passed:
        if not assumption_result.get("passed"):
            repair_targets.append("assumption_usage")
        if not computation_result.get("passed"):
            repair_targets.append("calculation")
        if not logic_result.get("passed"):
            repair_targets.append("logic")
        repair_targets = _dedupe(repair_targets)
    result = {
        "tool": "verify_overall",
        "status": "completed",
        "passed": passed,
        "overall_verdict": "correct" if passed else "incorrect",
        "failure_policy": "verify_overall always runs assumption, computation, and logic verification; all three dimensions must pass under pessimistic any-reviewer failure semantics.",
        "claim": shorten(str(claim), 5900),
        "project_slug": resolved_project,
        "reviewed_at": utc_now(),
        "scope": resolved_scope,
        "blueprint_path": resolved_blueprint,
        "assumption_result": assumption_result,
        "computation_result": computation_result,
        "logic_result": logic_result,
        "repair_targets": repair_targets,
        "summary": (
            "Overall verification passed: assumption use, calculation correctness, and logical correctness all passed."
            if passed
            else "Overall verification failed: at least one of assumption use, calculation correctness, or logical correctness did not pass."
        ),
    }
    validate_json_schema(result, OVERALL_VERIFICATION_RESULT_SCHEMA)
    return result
