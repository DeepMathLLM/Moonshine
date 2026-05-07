"""Skill-management tools for Moonshine."""

from __future__ import annotations

from typing import List


def _normalize_tags(tags) -> List[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        return [item.strip() for item in tags.split(",") if item.strip()]
    return [str(item).strip() for item in list(tags) if str(item).strip()]


def manage_skill(
    runtime: dict,
    operation: str,
    slug: str,
    title: str = "",
    description: str = "",
    body: str = "",
    category: str = "installed",
    tags=None,
    overwrite: bool = False,
    summary=None,
    execution_steps=None,
    tool_calls=None,
    file_references=None,
    compatibility: str = "",
    allowed_tools=None,
    purpose=None,
    when_to_use=None,
    inputs=None,
    workflow=None,
    checklist=None,
    output_contract=None,
    examples=None,
    notes=None,
    old_text: str = "",
    new_text: str = "",
    replace_all: bool = False,
    relative_path: str = "",
    content: str = "",
) -> dict:
    """Manage installed skills through a single structured tool."""
    manager = runtime["skill_manager"]
    normalized_operation = operation.strip().lower()

    if normalized_operation == "create":
        return manager.create_skill(
            slug=slug,
            title=title,
            description=description,
            body=body,
            category=category,
            tags=_normalize_tags(tags),
            overwrite=overwrite,
            summary=summary,
            execution_steps=execution_steps,
            tool_calls=tool_calls,
            file_references=file_references,
            compatibility=compatibility,
            allowed_tools=allowed_tools,
            purpose=purpose,
            when_to_use=when_to_use,
            inputs=inputs,
            workflow=workflow,
            checklist=checklist,
            output_contract=output_contract,
            examples=examples,
            notes=notes,
        )
    if normalized_operation == "patch":
        return manager.patch_skill(
            slug=slug,
            old_text=old_text,
            new_text=new_text,
            replace_all=replace_all,
        )
    if normalized_operation == "edit":
        return manager.edit_skill(
            slug=slug,
            title=title or None,
            description=description or None,
            body=body or None,
            category=category or None,
            tags=_normalize_tags(tags) if tags is not None else None,
            summary=summary,
            execution_steps=execution_steps,
            tool_calls=tool_calls,
            file_references=file_references,
            compatibility=compatibility or None,
            allowed_tools=allowed_tools,
            purpose=purpose,
            when_to_use=when_to_use,
            inputs=inputs,
            workflow=workflow,
            checklist=checklist,
            output_contract=output_contract,
            examples=examples,
            notes=notes,
        )
    if normalized_operation == "delete":
        return manager.delete_skill(slug=slug)
    if normalized_operation == "write_file":
        return manager.write_skill_file(slug=slug, relative_path=relative_path, content=content)
    if normalized_operation == "delete_file":
        return manager.delete_skill_file(slug=slug, relative_path=relative_path)
    raise ValueError("unknown skill operation: %s" % operation)
