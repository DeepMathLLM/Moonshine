"""Static memory and rule file helpers."""

from __future__ import annotations

from typing import Optional

from moonshine.moonshine_constants import MoonshinePaths
from moonshine.utils import atomic_write, read_text, shorten


class StaticContextStore(object):
    """Manage global and project rule files."""

    def __init__(self, paths: MoonshinePaths):
        self.paths = paths

    def load_global_rules(self) -> str:
        """Return the global rules text."""
        text = read_text(self.paths.global_rules_file)
        if text.strip():
            return text
        return read_text(self.paths.legacy_global_rules_file)

    def load_project_rules(self, project_slug: str) -> str:
        """Return the project-specific rules text."""
        return read_text(self.paths.project_rules_file(project_slug))

    def promote_summary(self, summary: str, project_slug: Optional[str] = None) -> str:
        """Append a promoted summary to a rule file."""
        target = self.paths.project_rules_file(project_slug) if project_slug else self.paths.global_rules_file
        current = read_text(target).rstrip()
        updated = current + "\n\n## Promoted Notes\n- %s\n" % shorten(summary, 240)
        atomic_write(target, updated)
        return str(target)
