"""Helpers for markdown files with embedded JSON metadata."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Tuple

from moonshine.utils import read_text


METADATA_RE = re.compile(r"\A<!--\s*(\{.*?\})\s*-->\s*", re.DOTALL)


def parse_markdown_metadata(raw: str) -> Tuple[Dict[str, object], str]:
    """Return JSON metadata and body content from raw markdown text."""
    match = METADATA_RE.match(raw)
    if not match:
        return {}, raw.strip()

    metadata = json.loads(match.group(1))
    body = raw[match.end() :].lstrip()
    return dict(metadata), body.strip()


def load_markdown_metadata(path: Path) -> Tuple[Dict[str, object], str]:
    """Return JSON metadata and body content from a markdown file."""
    raw = read_text(path)
    return parse_markdown_metadata(raw)


def render_markdown_metadata(metadata: Dict[str, object], body: str) -> str:
    """Render a markdown document with embedded JSON metadata."""
    rendered_metadata = json.dumps(metadata, indent=2, ensure_ascii=False)
    rendered_body = (body or "").strip()
    if rendered_body:
        return "<!--\n%s\n-->\n%s\n" % (rendered_metadata, rendered_body)
    return "<!--\n%s\n-->\n" % rendered_metadata
