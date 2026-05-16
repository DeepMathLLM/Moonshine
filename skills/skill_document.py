"""Agent Skills-compatible SKILL.md parsing, rendering, and validation."""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

from moonshine.markdown_metadata import parse_markdown_metadata
from moonshine.utils import slugify


SKILL_STANDARD = "agentskills.io/v1"
BODY_SECTION_ORDER = (
    "Summary",
    "Execution Steps",
    "Tool Calls",
    "File References",
    "Output Contract",
    "Notes",
)
HEADING_RE = re.compile(r"(?m)^## ([^\n]+)\s*$")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
VALID_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
BULLET_RE = re.compile(r"(?m)^\s*[-*]\s+(.*)$")
BACKTICK_RE = re.compile(r"`([^`]+)`")


def _strip_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text


def _parse_simple_yaml(frontmatter: str) -> Dict[str, object]:
    """Parse the subset of YAML used by Agent Skills frontmatter."""
    metadata: Dict[str, object] = {}
    current_key = ""
    nested_map: Dict[str, str] = {}

    for raw_line in frontmatter.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent and current_key == "metadata":
            if ":" not in stripped:
                continue
            child_key, child_value = stripped.split(":", 1)
            nested_map[child_key.strip()] = _strip_quotes(child_value.strip())
            metadata["metadata"] = nested_map
            continue

        current_key = ""
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        normalized_key = key.strip()
        normalized_value = value.strip()
        if not normalized_value:
            current_key = normalized_key
            if normalized_key == "metadata":
                nested_map = {}
                metadata["metadata"] = nested_map
            else:
                metadata[normalized_key] = ""
            continue
        metadata[normalized_key] = _strip_quotes(normalized_value)
    return metadata


def parse_skill_document(raw: str) -> Tuple[Dict[str, object], str]:
    """Parse an Agent Skills-compatible document with legacy fallback."""
    match = FRONTMATTER_RE.match(raw)
    if match:
        frontmatter = match.group(1).strip()
        body = raw[match.end() :].lstrip()
        try:
            metadata = json.loads(frontmatter)
        except ValueError:
            metadata = _parse_simple_yaml(frontmatter)
        return dict(metadata or {}), body.strip()

    metadata, body = parse_markdown_metadata(raw)
    return dict(metadata or {}), body.strip()


def _render_frontmatter_value(value: str) -> str:
    text = str(value or "")
    if not text:
        return '""'
    if any(character in text for character in [":", "#", '"', "'"]) or text != text.strip():
        return json.dumps(text, ensure_ascii=False)
    return text


def render_skill_document(metadata: Dict[str, object], body: str) -> str:
    """Render a skill document using YAML frontmatter."""
    lines = [
        "---",
        "name: %s" % _render_frontmatter_value(str(metadata.get("name", ""))),
        "description: %s" % _render_frontmatter_value(str(metadata.get("description", ""))),
    ]
    if str(metadata.get("license", "")).strip():
        lines.append("license: %s" % _render_frontmatter_value(str(metadata.get("license", ""))))
    if str(metadata.get("compatibility", "")).strip():
        lines.append("compatibility: %s" % _render_frontmatter_value(str(metadata.get("compatibility", ""))))
    if str(metadata.get("allowed-tools", "")).strip():
        lines.append("allowed-tools: %s" % _render_frontmatter_value(str(metadata.get("allowed-tools", ""))))
    frontmatter_metadata = dict(metadata.get("metadata") or {})
    if frontmatter_metadata:
        lines.append("metadata:")
        for key in sorted(frontmatter_metadata):
            lines.append("  %s: %s" % (key, _render_frontmatter_value(str(frontmatter_metadata[key]))))
    lines.append("---")
    rendered_body = (body or "").strip()
    if rendered_body:
        return "\n".join(lines) + "\n\n" + rendered_body + "\n"
    return "\n".join(lines) + "\n"


def extract_skill_sections(body: str) -> Dict[str, str]:
    """Return second-level markdown sections from a skill body."""
    text = str(body or "")
    matches = list(HEADING_RE.finditer(text))
    sections: Dict[str, str] = {}
    for index, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[title] = text[start:end].strip()
    return sections


def extract_skill_section_items(body: str, section_title: str) -> List[str]:
    """Extract primary bullet items from one skill section."""
    section = extract_skill_sections(body).get(section_title, "")
    items: List[str] = []
    for match in BULLET_RE.finditer(section):
        line = match.group(1).strip()
        ticked = BACKTICK_RE.search(line)
        if ticked:
            items.append(ticked.group(1).strip())
            continue
        if ":" in line:
            line = line.split(":", 1)[0].strip()
        if line:
            items.append(line)
    return items


def _normalize_lines(value: object) -> List[str]:
    """Normalize strings or arrays into markdown lines."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).rstrip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    return [line.rstrip() for line in text.splitlines() if line.strip()]


def _strip_title_block(text: str) -> str:
    """Remove a leading H1 block before body normalization."""
    lines = str(text or "").splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    return "\n".join(lines).strip()


def _render_section(title: str, lines: List[str]) -> str:
    """Render a markdown section if it has content."""
    if not lines:
        return ""
    return "## %s\n%s" % (title, "\n".join(lines))


def build_skill_body(
    *,
    name: str,
    description: str,
    body: str = "",
    summary: object = None,
    execution_steps: object = None,
    tool_calls: object = None,
    file_references: object = None,
    output_contract: object = None,
    notes: object = None,
    legacy_purpose: object = None,
    legacy_workflow: object = None,
    legacy_checklist: object = None,
    legacy_inputs: object = None,
    legacy_examples: object = None,
) -> str:
    """Build a portable SKILL.md body with the recommended sections."""
    normalized_body = str(body or "").strip()
    if normalized_body and validate_skill_body(normalized_body)[0]:
        return normalized_body

    summary_lines = _normalize_lines(summary) or _normalize_lines(legacy_purpose) or [str(description or "").strip()]
    execution_lines = _normalize_lines(execution_steps)
    if not execution_lines:
        execution_lines = _normalize_lines(legacy_workflow) or _normalize_lines(legacy_checklist)
    if not execution_lines and normalized_body:
        body_lines = _normalize_lines(_strip_title_block(normalized_body))
        if body_lines:
            execution_lines = body_lines
    if not execution_lines:
        execution_lines = ["1. Identify the concrete task and goal.", "2. Follow the referenced workflow carefully.", "3. Return the requested result with traceable evidence."]

    tool_lines = _normalize_lines(tool_calls)
    file_lines = _normalize_lines(file_references)
    if not file_lines and legacy_inputs:
        file_lines = _normalize_lines(legacy_inputs)
    output_lines = _normalize_lines(output_contract) or _normalize_lines(legacy_examples)
    note_lines = _normalize_lines(notes)

    sections = [
        "# %s" % str(name).strip(),
        _render_section("Summary", summary_lines),
        _render_section("Execution Steps", execution_lines),
        _render_section("Tool Calls", tool_lines or ["- No specific tool calls are required unless the runtime selects them."]),
        _render_section("File References", file_lines or ["- No additional files are required."]),
        _render_section("Output Contract", output_lines),
        _render_section("Notes", note_lines),
    ]
    return "\n\n".join(section for section in sections if section).strip() + "\n"


def normalize_skill_metadata(
    *,
    name: str,
    description: str,
    compatibility: str = "",
    allowed_tools: object = None,
    metadata: Optional[Dict[str, object]] = None,
    category: str = "",
    tags: Optional[List[str]] = None,
    title: str = "",
    license_name: str = "",
) -> Dict[str, object]:
    """Build canonical Agent Skills-compatible metadata."""
    normalized_name = slugify(name, prefix="skill")
    tag_string = ", ".join(str(item).strip() for item in list(tags or []) if str(item).strip())
    extra_metadata = {str(key): str(value) for key, value in dict(metadata or {}).items() if str(key).strip()}
    extra_metadata.setdefault("title", str(title or normalized_name.replace("-", " ").title()).strip())
    if category:
        extra_metadata.setdefault("category", str(category).strip())
    if tag_string:
        extra_metadata.setdefault("tags", tag_string)
    extra_metadata.setdefault("skill-standard", SKILL_STANDARD)

    if isinstance(allowed_tools, list):
        allowed_tools_text = " ".join(str(item).strip() for item in allowed_tools if str(item).strip())
    else:
        allowed_tools_text = str(allowed_tools or "").strip()

    return {
        "name": normalized_name,
        "description": str(description or "").strip(),
        "license": str(license_name or "").strip(),
        "compatibility": str(compatibility or "").strip(),
        "allowed-tools": allowed_tools_text,
        "metadata": extra_metadata,
    }


def validate_skill_body(body: str) -> Tuple[bool, List[str]]:
    """Validate the markdown body of a portable skill."""
    errors: List[str] = []
    text = str(body or "").strip()
    if not text:
        return False, ["skill body cannot be empty"]
    first_line = text.splitlines()[0].strip()
    if not first_line.startswith("# "):
        errors.append("skill body must start with a level-1 markdown heading")

    headings = set(HEADING_RE.findall(text))
    required_sections = {"Summary", "Execution Steps", "Tool Calls", "File References"}
    missing = sorted(required_sections - headings)
    for section in missing:
        errors.append("missing required section '## %s'" % section)
    return len(errors) == 0, errors


def validate_skill_document(metadata: Dict[str, object], body: str, *, expected_name: str = "") -> List[str]:
    """Validate a skill document against the Agent Skills spec plus Moonshine conventions."""
    errors: List[str] = []
    name = str(metadata.get("name", "")).strip()
    description = str(metadata.get("description", "")).strip()
    if not name:
        errors.append("frontmatter field 'name' is required")
    elif len(name) > 64 or not VALID_NAME_RE.match(name):
        errors.append("frontmatter field 'name' must be lowercase letters, numbers, and single hyphens only")
    if expected_name and name and name != expected_name:
        errors.append("frontmatter field 'name' must match the parent directory name")
    if not description:
        errors.append("frontmatter field 'description' is required")
    elif len(description) > 1024:
        errors.append("frontmatter field 'description' must be at most 1024 characters")

    compatibility = str(metadata.get("compatibility", "") or "").strip()
    if compatibility and len(compatibility) > 500:
        errors.append("frontmatter field 'compatibility' must be at most 500 characters")

    metadata_map = metadata.get("metadata", {}) or {}
    if not isinstance(metadata_map, dict):
        errors.append("frontmatter field 'metadata' must be a key-value mapping")
    elif any(not isinstance(key, str) or not isinstance(value, str) for key, value in metadata_map.items()):
        errors.append("frontmatter field 'metadata' must contain only string keys and string values")

    is_valid_body, body_errors = validate_skill_body(body)
    if not is_valid_body:
        errors.extend(body_errors)
    return errors


def ensure_valid_skill_document(metadata: Dict[str, object], body: str, *, expected_name: str = "") -> None:
    """Raise a helpful error when a skill document is invalid."""
    errors = validate_skill_document(metadata, body, expected_name=expected_name)
    if errors:
        raise ValueError("invalid skill document: %s" % "; ".join(errors))
