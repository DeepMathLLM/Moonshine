"""Research workflow tools for Moonshine."""

from __future__ import annotations

from typing import Dict, List, Optional

from moonshine.json_schema import validate_json_schema
from moonshine.providers import OfflineProvider
from moonshine.structured_tasks import register_structured_task
from moonshine.utils import shorten, utc_now


PROBLEM_QUALITY_ASSESSMENT_SCHEMA: Dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "reviewer_id": {"type": "string", "minLength": 1},
        "review_status": {"type": "string", "enum": ["passed", "pending", "failed"]},
        "impact": {"type": "number"},
        "feasibility": {"type": "number"},
        "novelty": {"type": "number"},
        "richness": {"type": "number"},
        "overall": {"type": "number"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "weaknesses": {"type": "array", "items": {"type": "string"}},
        "required_refinements": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": [
        "reviewer_id",
        "review_status",
        "impact",
        "feasibility",
        "novelty",
        "richness",
        "overall",
        "strengths",
        "weaknesses",
        "required_refinements",
        "rationale",
        "confidence",
    ],
}


register_structured_task(
    task_name="problem-quality-assessment",
    schema_name="problem_quality_assessment",
    schema=PROBLEM_QUALITY_ASSESSMENT_SCHEMA,
    description="One schema-constrained quality-assessor review for a candidate research problem.",
)


def _quality_review_provider(runtime: dict):
    """Use the same provider selection policy as multidimensional verification."""
    return runtime.get("provider") if bool(runtime.get("verification_provider_inherit_from_main", True)) else (runtime.get("verification_provider") or runtime.get("provider"))


def _provider_available(provider) -> bool:
    return provider is not None and not isinstance(provider, OfflineProvider) and hasattr(provider, "generate_structured")


def _coerce_score(value: object) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        number = 0.0
    return max(0.0, min(number, 1.0))


def _normalize_quality_assessment(payload: Dict[str, object]) -> Dict[str, object]:
    assessment = dict(payload or {})
    assessment["reviewer_id"] = str(assessment.get("reviewer_id") or "quality-assessor")
    if assessment.get("review_status") not in {"passed", "pending", "failed"}:
        assessment["review_status"] = "pending"
    for key in ["impact", "feasibility", "novelty", "richness", "overall"]:
        assessment[key] = _coerce_score(assessment.get(key))
    for key in ["strengths", "weaknesses", "required_refinements"]:
        value = assessment.get(key)
        if isinstance(value, list):
            assessment[key] = [str(item) for item in value]
        elif value in (None, ""):
            assessment[key] = []
        else:
            assessment[key] = [str(value)]
    assessment["rationale"] = str(assessment.get("rationale") or "")
    try:
        assessment["confidence"] = float(assessment.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        assessment["confidence"] = 0.0
    validate_json_schema(assessment, PROBLEM_QUALITY_ASSESSMENT_SCHEMA)
    return assessment


def _failure_quality_assessment(reason: str) -> Dict[str, object]:
    return {
        "reviewer_id": "quality-assessor",
        "review_status": "pending",
        "impact": 0.0,
        "feasibility": 0.0,
        "novelty": 0.0,
        "richness": 0.0,
        "overall": 0.0,
        "strengths": [],
        "weaknesses": [shorten(reason, 1200)],
        "required_refinements": ["Run the quality assessment again with an available structured LLM reviewer."],
        "rationale": shorten(reason, 2000),
        "confidence": 0.0,
    }


def assess_problem_quality(
    runtime: dict,
    problem: str,
    context: str = "",
    project_slug: str = "",
    set_as_active: bool = True,
) -> dict:
    """Run one dedicated quality-assessor review and persist it as problem_review."""
    resolved_project = str(project_slug or runtime.get("project_slug") or "general")
    provider = _quality_review_provider(runtime)
    if not _provider_available(provider):
        assessment = _failure_quality_assessment("No structured LLM provider is available for quality-assessor review.")
    else:
        prompt = (
            "Run exactly one dedicated quality-assessor review for the candidate research problem.\n"
            "Score each dimension from 0 to 1 using weights: impact 40%, feasibility 25%, novelty 20%, richness 15%.\n"
            "Pass only if the problem is precise, nontrivial, feasible enough to attack now, and worth entering problem solving.\n\n"
            "Candidate problem:\n{problem}\n\n"
            "Context:\n{context}"
        ).format(problem=str(problem), context=str(context or "(none)"))
        try:
            payload = provider.generate_structured(
                system_prompt=(
                    "You are the `quality-assessor` skill for Moonshine research mode. "
                    "Return only a JSON object matching the supplied schema."
                ),
                messages=[{"role": "user", "content": prompt}],
                response_schema=PROBLEM_QUALITY_ASSESSMENT_SCHEMA,
                schema_name="problem_quality_assessment",
            )
            assessment = _normalize_quality_assessment(dict(payload))
        except Exception as exc:
            assessment = _failure_quality_assessment("Structured quality-assessor review failed or returned invalid output: %s" % exc)

    return {
        "tool": "assess_problem_quality",
        "status": "completed",
        "project_slug": resolved_project,
        "problem": shorten(str(problem), 5900),
        "review_count": 1,
        "passed": str(assessment.get("review_status") or "") == "passed",
        "review_status": str(assessment.get("review_status") or "pending"),
        "assessment": assessment,
        "summary": str(assessment.get("rationale") or ""),
        "archived": False,
    }


def record_research_artifact(
    runtime: dict,
    artifact_type: str,
    title: str,
    summary: str,
    content: str = "",
    stage: str = "",
    focus_activity: str = "",
    status: str = "recorded",
    review_status: str = "not_applicable",
    related_ids: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    next_action: str = "",
    set_as_active: bool = False,
    metadata: Optional[Dict[str, object]] = None,
) -> dict:
    """Deprecated explicit artifact writer.

    Research mode memory is produced only by the post-turn archival pass.
    """
    return {
        "tool": "record_research_artifact",
        "status": "deprecated",
        "archived": False,
        "message": "Explicit artifact recording is disabled; the archival pass manages research_log.jsonl.",
    }


def _record_fixed_artifact(
    runtime: dict,
    *,
    artifact_type: str,
    title: str,
    summary: str,
    content: str = "",
    stage: str = "",
    focus_activity: str = "",
    status: str = "recorded",
    review_status: str = "not_applicable",
    related_ids: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    next_action: str = "",
    set_as_active: bool = False,
    metadata: Optional[Dict[str, object]] = None,
) -> dict:
    """Persist one typed research artifact through the shared runtime path."""
    merged_tags = [str(item) for item in list(tags or []) if str(item).strip()]
    default_tag = artifact_type.replace("_", "-")
    if default_tag not in merged_tags:
        merged_tags.append(default_tag)
    return record_research_artifact(
        runtime,
        artifact_type=artifact_type,
        title=title,
        summary=summary,
        content=content,
        stage=stage,
        focus_activity=focus_activity,
        status=status,
        review_status=review_status,
        related_ids=related_ids,
        tags=merged_tags,
        next_action=next_action,
        set_as_active=set_as_active,
        metadata=metadata,
    )


def record_solve_attempt(runtime: dict, **kwargs) -> dict:
    """Persist a solve attempt as a structured research artifact."""
    return _record_fixed_artifact(runtime, artifact_type="solve_attempt", **kwargs)


def record_failed_path(runtime: dict, **kwargs) -> dict:
    """Persist a failed path as a structured research artifact."""
    return _record_fixed_artifact(runtime, artifact_type="failed_path", **kwargs)


def commit_turn(
    runtime: dict,
    title: str,
    summary: str,
    next_action: str = "",
    stage: str = "",
    focus_activity: str = "",
    status: str = "",
    branch_id: str = "",
    current_focus: str = "",
    current_claim: str = "",
    blocker: str = "",
    problem_draft: str = "",
    blueprint_draft: str = "",
    scratchpad: str = "",
    open_questions: Optional[List[str]] = None,
    failed_paths: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    related_ids: Optional[List[str]] = None,
) -> dict:
    """Commit one durable research turn into canonical workspace files and runtime state."""
    manager = runtime.get("research_workflow")
    if manager is None:
        raise RuntimeError("research_workflow runtime is unavailable")
    return manager.commit_turn(
        project_slug=str(runtime.get("project_slug", "") or "general"),
        session_id=str(runtime.get("session_id", "") or ""),
        title=title,
        summary=summary,
        next_action=next_action,
        stage=stage,
        focus_activity=focus_activity,
        status=status,
        branch_id=branch_id,
        current_focus=current_focus,
        current_claim=current_claim,
        blocker=blocker,
        problem_draft=problem_draft,
        blueprint_draft=blueprint_draft,
        scratchpad=scratchpad,
        open_questions=list(open_questions or []),
        failed_paths=list(failed_paths or []),
        tags=list(tags or []),
        related_ids=list(related_ids or []),
    )
