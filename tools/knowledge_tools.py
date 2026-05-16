"""Knowledge tools for Moonshine."""

from __future__ import annotations

def _effective_project_slug(runtime: dict, project_slug: str) -> str:
    """Resolve the effective project scope for one knowledge-layer call."""
    resolved = str(project_slug or runtime.get("project_slug") or "general").strip()
    return resolved or "general"

def add_knowledge(runtime: dict, title: str, statement: str, proof_sketch: str = "", project_slug: str = "general") -> dict:
    """Add a verified knowledge conclusion."""
    project_slug = _effective_project_slug(runtime, project_slug)
    workflow = runtime.get("research_workflow")
    if workflow is not None and str(runtime.get("mode") or "") == "research":
        raise RuntimeError("add_knowledge is not exposed in research mode; use verification tools and project research memory.")
    item_id = runtime["memory_manager"].knowledge_store.add_conclusion(
        title=title,
        statement=statement,
        proof_sketch=proof_sketch,
        project_slug=project_slug,
        source_type="tool",
        source_ref="provider_tool",
    )
    return {
        "id": item_id,
        "stored_as": "knowledge",
        "status": "verified",
        "path": str(runtime["memory_manager"].knowledge_store.entry_path(item_id)),
    }


def store_conclusion(
    runtime: dict,
    title: str,
    statement: str,
    proof_sketch: str = "",
    project_slug: str = "general",
    status: str = "partial",
) -> dict:
    """Store a structured conclusion in the knowledge layer or downgrade it to a project artifact."""
    project_slug = _effective_project_slug(runtime, project_slug)
    workflow = runtime.get("research_workflow")
    if workflow is not None and str(runtime.get("mode") or "") == "research":
        raise RuntimeError("store_conclusion is not exposed in research mode; use verification tools and project research memory.")
    item_id = runtime["memory_manager"].knowledge_store.add_conclusion(
        title=title,
        statement=statement,
        proof_sketch=proof_sketch,
        project_slug=project_slug,
        status=status,
        source_type="skill",
        source_ref="conclusion-manage",
    )
    return {
        "id": item_id,
        "path": str(runtime["memory_manager"].knowledge_store.entry_path(item_id)),
        "stored_as": "knowledge",
        "status": status,
    }


def search_knowledge(runtime: dict, query: str, project_slug: str = "") -> dict:
    """Search knowledge conclusions."""
    rows = runtime["memory_manager"].knowledge_store.search(
        query,
        project_slug=(_effective_project_slug(runtime, project_slug) if project_slug else None),
        limit=5,
    )
    return {"results": rows}
