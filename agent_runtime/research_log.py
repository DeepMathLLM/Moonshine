"""Append-only research-log storage and retrieval."""

from __future__ import annotations

import json
import re
import sqlite3
from hashlib import sha1
from typing import Dict, Iterable, List, Optional, Sequence

from moonshine.utils import append_jsonl, atomic_write, overlap_score, read_jsonl, read_text, shorten, tokenize, utc_now


RESEARCH_LOG_TYPES = [
    "problem",
    "verified_conclusion",
    "verification",
    "final_result",
    "counterexample",
    "failed_path",
    "research_note",
]


def normalize_research_log_type(value: str) -> str:
    """Return a canonical research-log type."""
    normalized = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    aliases = {
        "conclusion": "verified_conclusion",
        "lemma": "verified_conclusion",
        "intermediate_conclusion": "verified_conclusion",
        "verify": "verification",
        "verification_reports": "verification",
        "result": "final_result",
        "final": "final_result",
        "note": "research_note",
        "progress": "research_note",
        "method_progress": "research_note",
        "failed_paths": "failed_path",
        "solve_steps": "research_note",
        "subgoals": "research_note",
        "branch_states": "research_note",
        "special_case_checks": "research_note",
        "novelty_notes": "research_note",
    }
    canonical = aliases.get(normalized, normalized)
    return canonical if canonical in RESEARCH_LOG_TYPES else "research_note"


def _hash_text(text: str) -> str:
    return sha1(str(text or "").encode("utf-8")).hexdigest()[:16]


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _safe_json_loads(text: str, default: object) -> object:
    try:
        return json.loads(text or "")
    except Exception:
        return default


def _best_excerpt(text: str, query: str, *, char_budget: int = 2200) -> str:
    """Return a local excerpt around the best lexical match."""
    source = str(text or "").strip()
    if not source:
        return ""
    budget = max(400, int(char_budget or 2200))
    if len(source) <= budget:
        return source
    lowered = source.lower()
    normalized_query = str(query or "").strip().lower()
    index = lowered.find(normalized_query) if normalized_query else -1
    if index < 0:
        tokens = [token for token in tokenize(query) if len(token) >= 3]
        best_score = -1.0
        best_index = -1
        for token in tokens[:16]:
            pos = lowered.find(token.lower())
            if pos < 0:
                continue
            window = source[max(0, pos - budget // 2) : min(len(source), pos + budget // 2)]
            score = overlap_score(query, window)
            if score > best_score:
                best_score = score
                best_index = pos
        index = best_index
    if index < 0:
        index = 0
    start = max(0, index - budget // 2)
    end = min(len(source), start + budget)
    start = max(0, end - budget)
    prefix = "[...]\n" if start > 0 else ""
    suffix = "\n[...]" if end < len(source) else ""
    return prefix + source[start:end].strip() + suffix


class ResearchLogStore(object):
    """Persist simple per-turn research records and search them."""

    def __init__(self, paths, knowledge_store=None):
        self.paths = paths
        self.knowledge_store = knowledge_store

    def log_path(self, project_slug: str):
        return self.paths.project_research_log_file(project_slug)

    def markdown_path(self, project_slug: str):
        return self.paths.project_research_log_markdown_file(project_slug)

    def index_path(self, project_slug: str):
        return self.paths.project_research_log_index_file(project_slug)

    def summaries_path(self, project_slug: str):
        return self.paths.project_research_log_summaries_file(project_slug)

    def _record_rows(self, project_slug: str) -> List[Dict[str, object]]:
        return [
            dict(item)
            for item in read_jsonl(self.log_path(project_slug))
            if isinstance(item, dict)
        ]

    def _summary_rows(self, project_slug: str) -> List[Dict[str, object]]:
        return [
            dict(item)
            for item in read_jsonl(self.summaries_path(project_slug))
            if isinstance(item, dict)
        ]

    def _connect(self, project_slug: str):
        path = self.index_path(project_slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self, conn) -> bool:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_log_docs (
                id TEXT PRIMARY KEY,
                project_slug TEXT,
                session_id TEXT,
                round_id TEXT,
                record_type TEXT,
                title TEXT,
                content TEXT,
                source_refs_json TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_research_log_project ON research_log_docs(project_slug)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_research_log_type ON research_log_docs(record_type)")
        fts_available = True
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS research_log_fts
                USING fts5(id UNINDEXED, title, content, record_type)
                """
            )
        except sqlite3.OperationalError:
            fts_available = False
        conn.commit()
        return fts_available

    def _upsert_record(self, conn, record: Dict[str, object], *, fts_available: bool) -> None:
        record_id = str(record.get("id") or "").strip()
        if not record_id:
            return
        record_type = normalize_research_log_type(str(record.get("type") or "research_note"))
        conn.execute(
            """
            INSERT OR REPLACE INTO research_log_docs
            (id, project_slug, session_id, round_id, record_type, title, content, source_refs_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                str(record.get("project_slug") or ""),
                str(record.get("session_id") or ""),
                str(record.get("round_id") or ""),
                record_type,
                str(record.get("title") or ""),
                str(record.get("content") or ""),
                _json_dumps(list(record.get("source_refs") or [])),
                str(record.get("created_at") or ""),
            ),
        )
        if fts_available:
            conn.execute("DELETE FROM research_log_fts WHERE id = ?", (record_id,))
            conn.execute(
                "INSERT INTO research_log_fts(id, title, content, record_type) VALUES (?, ?, ?, ?)",
                (
                    record_id,
                    str(record.get("title") or ""),
                    str(record.get("content") or ""),
                    record_type,
                ),
            )

    def rebuild_index(self, project_slug: str) -> Dict[str, object]:
        """Rebuild the project research-log index from research_log.jsonl."""
        conn = self._connect(project_slug)
        try:
            fts_available = self._ensure_schema(conn)
            conn.execute("DELETE FROM research_log_docs")
            if fts_available:
                conn.execute("DELETE FROM research_log_fts")
            count = 0
            for record in self.records(project_slug):
                self._upsert_record(conn, record, fts_available=fts_available)
                count += 1
            conn.commit()
            return {"project_slug": project_slug, "indexed": count, "fts": fts_available}
        finally:
            conn.close()

    def records(self, project_slug: str) -> List[Dict[str, object]]:
        """Return all records for one project."""
        return [dict(item) for item in self._record_rows(project_slug)]

    def append_summary(self, project_slug: str, summary: Dict[str, object]) -> None:
        """Append one reusable compression summary into research_log_summaries.jsonl."""
        if not isinstance(summary, dict):
            return
        append_jsonl(self.summaries_path(project_slug), dict(summary))

    def latest_summary(self, project_slug: str) -> Dict[str, object]:
        summaries = self._summary_rows(project_slug)
        return summaries[-1] if summaries else {}

    def _format_record_md(self, record: Dict[str, object]) -> str:
        refs = list(record.get("source_refs") or [])
        ref_lines = ["- `%s`" % str(item) for item in refs if str(item).strip()]
        if not ref_lines:
            ref_lines = ["- (none recorded)"]
        return (
            "## {created_at} UTC / {record_id} / {record_type}\n\n"
            "Title: {title}\n\n"
            "{content}\n\n"
            "Source refs:\n{refs}\n\n"
        ).format(
            created_at=str(record.get("created_at") or ""),
            record_id=str(record.get("id") or ""),
            record_type=str(record.get("type") or ""),
            title=str(record.get("title") or ""),
            content=str(record.get("content") or "").strip(),
            refs="\n".join(ref_lines),
        )

    def append_records(self, project_slug: str, records: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
        """Append normalized records to research_log.jsonl and markdown views."""
        all_records = self._record_rows(project_slug)
        existing_ids = {str(item.get("id") or "") for item in all_records}
        created_records: List[Dict[str, object]] = []
        for raw in records:
            if not isinstance(raw, dict):
                continue
            content = str(raw.get("content") or "").strip()
            title = str(raw.get("title") or "").strip() or shorten(content, 80) or "Research record"
            if not content:
                continue
            record_type = normalize_research_log_type(str(raw.get("type") or "research_note"))
            created_at = str(raw.get("created_at") or utc_now())
            source_refs = [str(item) for item in list(raw.get("source_refs") or []) if str(item).strip()]
            base_id = str(raw.get("id") or "").strip()
            record_id = base_id or "rec-%s" % _hash_text(
                "%s\n%s\n%s\n%s" % (project_slug, record_type, title, content)
            )
            if record_id in existing_ids:
                continue
            record = {
                "id": record_id,
                "created_at": created_at,
                "project_slug": project_slug,
                "session_id": str(raw.get("session_id") or ""),
                "round_id": str(raw.get("round_id") or ""),
                "type": record_type,
                "title": title,
                "content": content,
                "source_refs": source_refs,
            }
            tool_signature = str(raw.get("tool_signature") or "").strip()
            if tool_signature:
                record["tool_signature"] = tool_signature
            existing_ids.add(record_id)
            created_records.append(record)
            all_records.append(record)

            if record_type == "verified_conclusion":
                self._mirror_verified_conclusion(project_slug, record)

        if created_records:
            for record in created_records:
                append_jsonl(self.log_path(project_slug), record)
            self.rebuild_markdown_views(project_slug)
            self._sync_blueprint_markdown(project_slug)
            self.rebuild_index(project_slug)
        return created_records

    def rebuild_markdown_views(self, project_slug: str) -> Dict[str, object]:
        """Rebuild research_log.md and by_type/*.md from research_log.jsonl."""
        records = self.records(project_slug)
        full_chunks = ["# Research Log"]
        by_type: Dict[str, List[str]] = {record_type: [] for record_type in RESEARCH_LOG_TYPES}
        for record in records:
            normalized_type = normalize_research_log_type(str(record.get("type") or "research_note"))
            normalized_record = dict(record)
            normalized_record["type"] = normalized_type
            rendered = self._format_record_md(normalized_record).strip()
            if not rendered:
                continue
            full_chunks.append(rendered)
            by_type.setdefault(normalized_type, []).append(rendered)

        atomic_write(self.markdown_path(project_slug), "\n\n".join(full_chunks).rstrip() + "\n")
        by_type_dir = self.paths.project_research_log_type_file(project_slug, "research_note").parent
        by_type_dir.mkdir(parents=True, exist_ok=True)
        for record_type in RESEARCH_LOG_TYPES:
            path = self.paths.project_research_log_type_file(project_slug, record_type)
            header = "# %s" % record_type.replace("_", " ").title()
            body = "\n\n".join(by_type.get(record_type, []))
            atomic_write(path, (header + ("\n\n" + body if body else "") + "\n").rstrip() + "\n")
        return {"project_slug": project_slug, "records": len(records), "types": len(RESEARCH_LOG_TYPES)}

    def _sync_blueprint_markdown(self, project_slug: str) -> None:
        """Keep workspace/blueprint.md as the readable research-log mirror."""
        text = read_text(self.markdown_path(project_slug), default="")
        atomic_write(self.paths.project_blueprint_file(project_slug), text.rstrip() + ("\n" if text.strip() else ""))

    def _mirror_verified_conclusion(self, project_slug: str, record: Dict[str, object]) -> None:
        if self.knowledge_store is None:
            return
        content = str(record.get("content") or "").strip()
        if not content:
            return
        source_ref = ",".join(str(item) for item in list(record.get("source_refs") or []) if str(item).strip())
        try:
            self.knowledge_store.add_conclusion(
                title=str(record.get("title") or "Verified conclusion"),
                statement=content,
                proof_sketch="",
                status="verified",
                project_slug=project_slug,
                tags=["research-log", "verified-conclusion"],
                source_type="research_log",
                source_ref=source_ref or str(record.get("id") or ""),
            )
        except Exception:
            pass

    def search(
        self,
        *,
        query: str,
        project_slug: Optional[str],
        types: Optional[Sequence[str]] = None,
        limit: int = 5,
    ) -> List[Dict[str, object]]:
        """Search research-log records, returning content directly."""
        selected_types = [normalize_research_log_type(item) for item in list(types or []) if str(item).strip()]
        selected_types = list(dict.fromkeys(selected_types))
        rows: List[Dict[str, object]] = []
        projects = [project_slug] if project_slug else self._all_projects()
        for slug in [str(item) for item in projects if str(item or "").strip()]:
            rows.extend(self._search_one_project(slug, query=query, types=selected_types, limit=limit))
        rows.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("created_at") or "")))
        return rows[: max(1, int(limit or 5))]

    def _all_projects(self) -> List[str]:
        if not self.paths.projects_dir.exists():
            return []
        return [item.name for item in sorted(self.paths.projects_dir.iterdir()) if item.is_dir()]

    def _search_one_project(self, project_slug: str, *, query: str, types: Sequence[str], limit: int) -> List[Dict[str, object]]:
        self.rebuild_index(project_slug)
        conn = self._connect(project_slug)
        try:
            fts_available = self._ensure_schema(conn)
            filters = ["project_slug = ?"]
            params: List[object] = [project_slug]
            if types:
                filters.append("record_type IN (%s)" % ", ".join("?" for _ in types))
                params.extend(types)
            where = " AND ".join(filters)
            clean_query = str(query or "").strip()
            if clean_query and fts_available:
                tokens = [re.sub(r"[^A-Za-z0-9_]+", "", token) for token in tokenize(clean_query)]
                tokens = [token for token in tokens if token]
                if tokens:
                    fts_query = " OR ".join(tokens[:16])
                    try:
                        rows = conn.execute(
                            """
                            SELECT d.*, bm25(research_log_fts) AS rank_score
                            FROM research_log_fts
                            JOIN research_log_docs d ON d.id = research_log_fts.id
                            WHERE research_log_fts MATCH ?
                              AND %s
                            ORDER BY rank_score ASC
                            LIMIT ?
                            """
                            % where,
                            [fts_query] + params + [max(1, int(limit or 5))],
                        ).fetchall()
                        if rows:
                            return [self._coerce_row(row, query=clean_query, score=1.0 / float(index + 1)) for index, row in enumerate(rows)]
                    except sqlite3.OperationalError:
                        pass
            sql = "SELECT * FROM research_log_docs WHERE %s" % where
            rows = conn.execute(sql, params).fetchall()
            return self._fallback_rank(rows, clean_query, limit=limit)
        finally:
            conn.close()

    def _fallback_rank(self, rows: Sequence[sqlite3.Row], query: str, *, limit: int) -> List[Dict[str, object]]:
        ranked = []
        for row in rows:
            blob = "\n".join([str(row["title"] or ""), str(row["content"] or ""), str(row["record_type"] or "")])
            score = overlap_score(query, blob) if str(query or "").strip() else 1.0
            if score <= 0.0 and str(query or "").strip():
                continue
            ranked.append(self._coerce_row(row, query=query, score=score))
        ranked.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("created_at") or "")))
        return ranked[: max(1, int(limit or 5))]

    def _coerce_row(self, row: sqlite3.Row, *, query: str, score: float) -> Dict[str, object]:
        refs = list(_safe_json_loads(str(row["source_refs_json"] or ""), []))
        content = str(row["content"] or "")
        return {
            "id": str(row["id"] or ""),
            "key": "research-log:%s" % str(row["id"] or ""),
            "source": "research-log",
            "source_type": "research_log",
            "artifact_type": str(row["record_type"] or "research_note"),
            "type": str(row["record_type"] or "research_note"),
            "title": str(row["title"] or ""),
            "content": content,
            "content_inline": _best_excerpt(content, query),
            "content_path": self.paths.project_research_log_file(str(row["project_slug"] or "")).relative_to(self.paths.home).as_posix(),
            "project_slug": str(row["project_slug"] or ""),
            "session_id": str(row["session_id"] or ""),
            "round_id": str(row["round_id"] or ""),
            "source_refs": refs,
            "created_at": str(row["created_at"] or ""),
            "score": float(score or 0.0),
            "metadata": {
                "source_type": "research_log",
                "record_type": str(row["record_type"] or "research_note"),
                "source_refs": refs,
                "source_path": self.paths.project_research_log_file(str(row["project_slug"] or "")).relative_to(self.paths.home).as_posix(),
                "exact_excerpt": _best_excerpt(content, query),
                "raw_text": content,
            },
        }


RESEARCH_ARCHIVE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "records": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {"type": "string", "enum": RESEARCH_LOG_TYPES},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["type", "title", "content"],
            },
        }
    },
    "required": ["records"],
}


def render_research_log_for_archive(records: Sequence[Dict[str, object]]) -> str:
    """Render existing research-log records for an archival call."""
    lines = []
    for item in records:
        lines.append(
            "[{record_type}] {title}\n{content}".format(
                record_type=str(item.get("type") or "research_note"),
                title=str(item.get("title") or ""),
                content=str(item.get("content") or ""),
            )
        )
    return "\n\n---\n\n".join(lines)
