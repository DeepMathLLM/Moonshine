"""Memory review helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List

from moonshine.storage.dynamic_memory_store import DynamicMemoryEntry
from moonshine.utils import jaccard_similarity


@dataclass
class ReviewReport:
    """Summary of potential memory maintenance actions."""

    stale_entries: List[DynamicMemoryEntry] = field(default_factory=list)
    duplicate_pairs: List[str] = field(default_factory=list)
    promotion_candidates: List[DynamicMemoryEntry] = field(default_factory=list)

    def to_text(self) -> str:
        """Render a human-readable report."""
        lines = ["Memory review report", ""]
        lines.append("Stale entries: %s" % len(self.stale_entries))
        if self.stale_entries:
            lines.extend("- %s" % item.slug for item in self.stale_entries[:5])
        lines.append("")
        lines.append("Potential duplicate pairs: %s" % len(self.duplicate_pairs))
        if self.duplicate_pairs:
            lines.extend("- %s" % item for item in self.duplicate_pairs[:5])
        lines.append("")
        lines.append("Promotion candidates: %s" % len(self.promotion_candidates))
        if self.promotion_candidates:
            lines.extend("- %s (%s)" % (item.title, item.slug) for item in self.promotion_candidates[:5])
        return "\n".join(lines)


def _parse_timestamp(value: str) -> datetime:
    cleaned = value.replace("Z", "")
    return datetime.fromisoformat(cleaned)


def build_review_report(entries: List[DynamicMemoryEntry], stale_days: int = 14) -> ReviewReport:
    """Build a basic review report for dynamic memory."""
    now = datetime.utcnow()
    stale_cutoff = now - timedelta(days=stale_days)
    stale_entries = []
    for entry in entries:
        try:
            if _parse_timestamp(entry.updated_at) < stale_cutoff:
                stale_entries.append(entry)
        except ValueError:
            continue

    duplicate_pairs = []
    for index, left in enumerate(entries):
        for right in entries[index + 1 :]:
            if left.file_alias != right.file_alias:
                continue
            score = jaccard_similarity(left.summary or left.body, right.summary or right.body)
            if score >= 0.75:
                duplicate_pairs.append("%s <-> %s" % (left.slug, right.slug))

    promotion_candidates = [
        item
        for item in entries
        if item.file_alias in {"feedback-explicit", "feedback-success", "project-lemmas"}
    ]
    return ReviewReport(
        stale_entries=stale_entries,
        duplicate_pairs=duplicate_pairs,
        promotion_candidates=promotion_candidates[:10],
    )
