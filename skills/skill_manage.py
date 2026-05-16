"""Standalone skill-file management for Agent Skills-compatible skills."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from moonshine.moonshine_cli.config import resolve_home
from moonshine.moonshine_constants import MoonshinePaths
from moonshine.skills.skill_document import parse_skill_document, render_skill_document
from moonshine.skills.template import build_skill_body, ensure_valid_skill_document, normalize_skill_metadata
from moonshine.utils import append_jsonl, atomic_write, ensure_directory, read_text, slugify, utc_now


class SkillFileManager(object):
    """Create, patch, edit, and delete installed skill files."""

    def __init__(self, paths: MoonshinePaths):
        self.paths = paths

    def _normalize_slug(self, slug: str) -> str:
        normalized = slugify(slug, prefix="skill")
        if not normalized:
            raise ValueError("skill slug cannot be empty")
        return normalized

    def _skill_dir(self, slug: str) -> Path:
        return self.paths.installed_skills_dir / self._normalize_slug(slug)

    def _skill_file(self, slug: str) -> Path:
        return self._skill_dir(slug) / "SKILL.md"

    def _resolve_skill_path(self, slug: str, relative_path: str) -> Path:
        target = (self._skill_dir(slug) / relative_path).resolve()
        root = self._skill_dir(slug).resolve()
        if target != root and root not in target.parents:
            raise ValueError("path escapes the skill directory")
        return target

    def _record(self, event: str, **payload: object) -> None:
        append_jsonl(
            self.paths.skills_audit_log,
            {
                "event": event,
                "created_at": utc_now(),
                **dict(payload),
            },
        )

    def create_skill(
        self,
        *,
        slug: str,
        title: str,
        description: str,
        body: str = "",
        category: str = "installed",
        tags: Optional[List[str]] = None,
        overwrite: bool = False,
        summary: object = None,
        execution_steps: object = None,
        tool_calls: object = None,
        file_references: object = None,
        output_contract: object = None,
        notes: object = None,
        compatibility: str = "",
        allowed_tools: object = None,
        purpose: object = None,
        when_to_use: object = None,
        inputs: object = None,
        workflow: object = None,
        checklist: object = None,
        examples: object = None,
    ) -> Dict[str, object]:
        """Create a new installed skill."""
        normalized_slug = self._normalize_slug(slug)
        skill_file = self._skill_file(normalized_slug)
        if skill_file.exists() and not overwrite:
            raise FileExistsError("skill already exists: %s" % normalized_slug)

        normalized_title = title.strip() or normalized_slug.replace("-", " ").title()
        metadata = normalize_skill_metadata(
            name=normalized_slug,
            description=description.strip(),
            compatibility=compatibility,
            allowed_tools=allowed_tools,
            category=category.strip() or "installed",
            tags=[str(item).strip() for item in list(tags or []) if str(item).strip()],
            title=normalized_title,
        )
        rendered_body = build_skill_body(
            name=normalized_title,
            description=description.strip(),
            body=body,
            summary=summary,
            execution_steps=execution_steps,
            tool_calls=tool_calls,
            file_references=file_references,
            output_contract=output_contract,
            notes=notes,
            legacy_purpose=purpose or when_to_use,
            legacy_workflow=workflow,
            legacy_checklist=checklist,
            legacy_inputs=inputs,
            legacy_examples=examples,
        )
        ensure_valid_skill_document(metadata, rendered_body, expected_name=normalized_slug)
        ensure_directory(skill_file.parent)
        atomic_write(skill_file, render_skill_document(metadata, rendered_body))
        self._record("skill_create", slug=normalized_slug, path=str(skill_file))
        return {"operation": "create", "slug": normalized_slug, "path": str(skill_file)}

    def patch_skill(
        self,
        *,
        slug: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
    ) -> Dict[str, object]:
        """Patch a skill by replacing exact text."""
        skill_file = self._skill_file(slug)
        current = read_text(skill_file)
        if not current:
            raise FileNotFoundError("skill not found: %s" % self._normalize_slug(slug))
        occurrence_count = current.count(old_text)
        if occurrence_count <= 0:
            raise ValueError("patch target not found in %s" % skill_file)
        updated = current.replace(old_text, new_text, -1 if replace_all else 1)
        metadata, body = parse_skill_document(updated)
        nested_metadata = dict(metadata.get("metadata") or {})
        metadata = normalize_skill_metadata(
            name=self._normalize_slug(slug),
            description=str(metadata.get("description", "")).strip(),
            compatibility=str(metadata.get("compatibility", "")).strip(),
            allowed_tools=str(metadata.get("allowed-tools", "")).strip(),
            category=str(nested_metadata.get("category", "installed")).strip(),
            tags=[item.strip() for item in str(nested_metadata.get("tags", "") or "").split(",") if item.strip()],
            title=str(nested_metadata.get("title", "")).strip(),
            metadata=nested_metadata,
            license_name=str(metadata.get("license", "")).strip(),
        )
        ensure_valid_skill_document(metadata, body, expected_name=self._normalize_slug(slug))
        updated = render_skill_document(metadata, body)
        atomic_write(skill_file, updated)
        self._record(
            "skill_patch",
            slug=self._normalize_slug(slug),
            path=str(skill_file),
            replace_all=replace_all,
            replaced=occurrence_count if replace_all else 1,
        )
        return {
            "operation": "patch",
            "slug": self._normalize_slug(slug),
            "path": str(skill_file),
            "replaced": occurrence_count if replace_all else 1,
        }

    def edit_skill(
        self,
        *,
        slug: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        body: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        summary: object = None,
        execution_steps: object = None,
        tool_calls: object = None,
        file_references: object = None,
        output_contract: object = None,
        notes: object = None,
        compatibility: Optional[str] = None,
        allowed_tools: object = None,
        purpose: object = None,
        when_to_use: object = None,
        inputs: object = None,
        workflow: object = None,
        checklist: object = None,
        examples: object = None,
    ) -> Dict[str, object]:
        """Rewrite selected metadata or the skill body."""
        skill_file = self._skill_file(slug)
        if not skill_file.exists():
            raise FileNotFoundError("skill not found: %s" % self._normalize_slug(slug))

        metadata, current_body = parse_skill_document(read_text(skill_file))
        nested_metadata = dict(metadata.get("metadata") or {})
        normalized_title = title.strip() if title is not None else str(nested_metadata.get("title", "")).strip()
        normalized_description = description.strip() if description is not None else str(metadata.get("description", "")).strip()
        normalized_category = category.strip() if category is not None else str(nested_metadata.get("category", "installed")).strip()
        normalized_tags = [str(item).strip() for item in list(tags) if str(item).strip()] if tags is not None else [item.strip() for item in str(nested_metadata.get("tags", "") or "").split(",") if item.strip()]

        normalized_metadata = normalize_skill_metadata(
            name=self._normalize_slug(slug),
            description=normalized_description,
            compatibility=compatibility if compatibility is not None else str(metadata.get("compatibility", "")).strip(),
            allowed_tools=allowed_tools if allowed_tools is not None else str(metadata.get("allowed-tools", "")).strip(),
            category=normalized_category,
            tags=normalized_tags,
            title=normalized_title,
            metadata=nested_metadata,
            license_name=str(metadata.get("license", "")).strip(),
        )
        rendered_body = build_skill_body(
            name=normalized_title,
            description=normalized_description,
            body=body if body is not None else current_body,
            summary=summary,
            execution_steps=execution_steps,
            tool_calls=tool_calls,
            file_references=file_references,
            output_contract=output_contract,
            notes=notes,
            legacy_purpose=purpose or when_to_use,
            legacy_workflow=workflow,
            legacy_checklist=checklist,
            legacy_inputs=inputs,
            legacy_examples=examples,
        )
        ensure_valid_skill_document(normalized_metadata, rendered_body, expected_name=self._normalize_slug(slug))
        atomic_write(skill_file, render_skill_document(normalized_metadata, rendered_body))
        self._record("skill_edit", slug=self._normalize_slug(slug), path=str(skill_file))
        return {"operation": "edit", "slug": self._normalize_slug(slug), "path": str(skill_file)}

    def delete_skill(self, *, slug: str) -> Dict[str, object]:
        """Delete an installed skill directory."""
        skill_dir = self._skill_dir(slug)
        if not skill_dir.exists():
            raise FileNotFoundError("skill not found: %s" % self._normalize_slug(slug))
        shutil.rmtree(str(skill_dir))
        self._record("skill_delete", slug=self._normalize_slug(slug), path=str(skill_dir))
        return {"operation": "delete", "slug": self._normalize_slug(slug), "path": str(skill_dir)}

    def write_skill_file(self, *, slug: str, relative_path: str, content: str) -> Dict[str, object]:
        """Write an auxiliary file inside a skill directory."""
        target = self._resolve_skill_path(slug, relative_path)
        if target.name == "SKILL.md":
            raise ValueError("write_skill_file cannot overwrite SKILL.md; use create_skill, patch_skill, or edit_skill")
        ensure_directory(target.parent)
        atomic_write(target, content)
        self._record("skill_write_file", slug=self._normalize_slug(slug), path=str(target))
        return {"operation": "write_file", "slug": self._normalize_slug(slug), "path": str(target)}

    def delete_skill_file(self, *, slug: str, relative_path: str) -> Dict[str, object]:
        """Delete an auxiliary file inside a skill directory."""
        target = self._resolve_skill_path(slug, relative_path)
        if target.name == "SKILL.md":
            raise ValueError("delete_skill_file cannot remove SKILL.md; use delete_skill instead")
        if not target.exists():
            raise FileNotFoundError("skill file not found: %s" % target)
        target.unlink()
        self._record("skill_delete_file", slug=self._normalize_slug(slug), path=str(target))
        return {"operation": "delete_file", "slug": self._normalize_slug(slug), "path": str(target)}

    def record_load(self, *, slug: str, session_id: str = "", project_slug: str = "") -> None:
        """Record on-demand skill loading for provenance."""
        self._record(
            "skill_load",
            slug=self._normalize_slug(slug),
            session_id=session_id,
            project_slug=project_slug,
        )


def build_parser() -> argparse.ArgumentParser:
    """Build the standalone skill-manage parser."""
    parser = argparse.ArgumentParser(prog="python -m moonshine.skills.skill_manage")
    parser.add_argument("--home", default=None, help="Moonshine runtime home.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--slug", required=True)
    create.add_argument("--title", required=True)
    create.add_argument("--description", required=True)
    create.add_argument("--body", default="")
    create.add_argument("--category", default="installed")
    create.add_argument("--tags", default="")
    create.add_argument("--overwrite", action="store_true")
    create.add_argument("--summary")
    create.add_argument("--execution-steps")
    create.add_argument("--tool-calls")
    create.add_argument("--file-references")
    create.add_argument("--compatibility")
    create.add_argument("--allowed-tools")
    create.add_argument("--purpose")
    create.add_argument("--when-to-use")
    create.add_argument("--inputs")
    create.add_argument("--workflow")
    create.add_argument("--checklist")
    create.add_argument("--output-contract")
    create.add_argument("--examples")
    create.add_argument("--notes")

    patch = subparsers.add_parser("patch")
    patch.add_argument("--slug", required=True)
    patch.add_argument("--old-text", required=True)
    patch.add_argument("--new-text", required=True)
    patch.add_argument("--replace-all", action="store_true")

    edit = subparsers.add_parser("edit")
    edit.add_argument("--slug", required=True)
    edit.add_argument("--title")
    edit.add_argument("--description")
    edit.add_argument("--body")
    edit.add_argument("--category")
    edit.add_argument("--tags")
    edit.add_argument("--summary")
    edit.add_argument("--execution-steps")
    edit.add_argument("--tool-calls")
    edit.add_argument("--file-references")
    edit.add_argument("--compatibility")
    edit.add_argument("--allowed-tools")
    edit.add_argument("--purpose")
    edit.add_argument("--when-to-use")
    edit.add_argument("--inputs")
    edit.add_argument("--workflow")
    edit.add_argument("--checklist")
    edit.add_argument("--output-contract")
    edit.add_argument("--examples")
    edit.add_argument("--notes")

    delete = subparsers.add_parser("delete")
    delete.add_argument("--slug", required=True)

    write_file = subparsers.add_parser("write-file")
    write_file.add_argument("--slug", required=True)
    write_file.add_argument("--path", required=True)
    write_file.add_argument("--content", required=True)

    delete_file = subparsers.add_parser("delete-file")
    delete_file.add_argument("--slug", required=True)
    delete_file.add_argument("--path", required=True)

    return parser


def _parse_tags(raw: Optional[str]) -> List[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for standalone skill management."""
    parser = build_parser()
    args = parser.parse_args(argv)
    paths = MoonshinePaths(resolve_home(args.home))
    manager = SkillFileManager(paths)

    if args.command == "create":
        result = manager.create_skill(
            slug=args.slug,
            title=args.title,
            description=args.description,
            body=args.body,
            category=args.category,
            tags=_parse_tags(args.tags),
            overwrite=args.overwrite,
            summary=args.summary,
            execution_steps=args.execution_steps,
            tool_calls=args.tool_calls,
            file_references=args.file_references,
            compatibility=args.compatibility or "",
            allowed_tools=args.allowed_tools or "",
            purpose=args.purpose,
            when_to_use=args.when_to_use,
            inputs=args.inputs,
            workflow=args.workflow,
            checklist=args.checklist,
            output_contract=args.output_contract,
            examples=args.examples,
            notes=args.notes,
        )
    elif args.command == "patch":
        result = manager.patch_skill(
            slug=args.slug,
            old_text=args.old_text,
            new_text=args.new_text,
            replace_all=bool(args.replace_all),
        )
    elif args.command == "edit":
        result = manager.edit_skill(
            slug=args.slug,
            title=args.title,
            description=args.description,
            body=args.body,
            category=args.category,
            tags=_parse_tags(args.tags) if args.tags is not None else None,
            summary=args.summary,
            execution_steps=args.execution_steps,
            tool_calls=args.tool_calls,
            file_references=args.file_references,
            compatibility=args.compatibility,
            allowed_tools=args.allowed_tools,
            purpose=args.purpose,
            when_to_use=args.when_to_use,
            inputs=args.inputs,
            workflow=args.workflow,
            checklist=args.checklist,
            output_contract=args.output_contract,
            examples=args.examples,
            notes=args.notes,
        )
    elif args.command == "delete":
        result = manager.delete_skill(slug=args.slug)
    elif args.command == "write-file":
        result = manager.write_skill_file(slug=args.slug, relative_path=args.path, content=args.content)
    else:
        result = manager.delete_skill_file(slug=args.slug, relative_path=args.path)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
