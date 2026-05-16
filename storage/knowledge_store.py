"""Structured knowledge storage for Moonshine."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from moonshine.moonshine_constants import MoonshinePaths
from moonshine.storage.knowledge_vector_store import KnowledgeVectorIndex
from moonshine.utils import append_jsonl, atomic_write, overlap_score, shorten, tokenize, utc_now


class KnowledgeStore(object):
    """Persist structured conclusions with SQLite and audit logs."""

    def __init__(self, paths: MoonshinePaths, config=None):
        self.paths = paths
        self.config = config
        self.db_path = paths.knowledge_db
        self.fts_enabled = False
        self.vector_index = KnowledgeVectorIndex(paths, config or object())
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA foreign_keys=ON")
        except sqlite3.OperationalError:
            pass
        return connection

    def _initialize(self) -> None:
        try:
            self._initialize_at(self.db_path)
        except sqlite3.OperationalError:
            fallback = Path(tempfile.gettempdir()) / (self.db_path.stem + "_fallback.sqlite3")
            self.db_path = fallback
            self._initialize_at(self.db_path)

    def _initialize_at(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.paths.knowledge_entries_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conclusions (
                    id TEXT PRIMARY KEY,
                    project_slug TEXT NOT NULL,
                    title TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    proof_sketch TEXT NOT NULL,
                    status TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            try:
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts
                    USING fts5(conclusion_id UNINDEXED, title, statement, proof_sketch, tags)
                    """
                )
                self.fts_enabled = True
            except sqlite3.OperationalError:
                self.fts_enabled = False

    def _entry_path(self, conclusion_id: str) -> Path:
        """Return the markdown path for a conclusion."""
        return self.paths.knowledge_entries_dir / ("%s.md" % conclusion_id)

    def entry_path(self, conclusion_id: str) -> Path:
        """Return the public markdown path for a conclusion."""
        return self._entry_path(conclusion_id)

    def _entry_markdown(
        self,
        *,
        conclusion_id: str,
        title: str,
        statement: str,
        proof_sketch: str,
        status: str,
        project_slug: str,
        tags: List[str],
        source_type: str,
        source_ref: str,
        created_at: str,
    ) -> str:
        """Render a structured conclusion as markdown."""
        metadata = {
            "id": conclusion_id,
            "project_slug": project_slug.strip(),
            "status": status.strip(),
            "source_type": source_type.strip(),
            "source_ref": source_ref.strip(),
            "created_at": created_at,
            "tags": list(tags),
        }
        return (
            "<!--\n"
            "{metadata}\n"
            "-->\n"
            "# {title}\n\n"
            "## Statement\n{statement}\n\n"
            "## Proof Sketch\n{proof_sketch}\n"
        ).format(
            metadata=json.dumps(metadata, indent=2, ensure_ascii=False),
            title=title.strip(),
            statement=statement.strip() or "(none)",
            proof_sketch=proof_sketch.strip() or "(none)",
        )

    def _row_to_item(self, row) -> Dict[str, object]:
        """Convert a SQLite conclusion row into a public item dict."""
        item = dict(row)
        item["tags"] = json.loads(item.pop("tags_json"))
        item["path"] = str(self.entry_path(item["id"]))
        return item

    def _vector_payload(self, item: Dict[str, object]) -> Dict[str, object]:
        """Build the vector-index payload for one conclusion item."""
        return {
            "id": item["id"],
            "project_slug": item["project_slug"],
            "title": item["title"],
            "statement": item["statement"],
            "proof_sketch": item["proof_sketch"],
            "status": item["status"],
            "tags": list(item.get("tags") or []),
            "source_type": item.get("source_type", ""),
            "source_ref": item.get("source_ref", ""),
            "updated_at": item.get("updated_at", ""),
        }

    def _upsert_vector_payload(self, item: Dict[str, object]) -> None:
        """Best-effort vector upsert with audit logging."""
        try:
            self.vector_index.upsert_conclusion(self._vector_payload(item))
            append_jsonl(
                self.paths.knowledge_audit_log,
                {
                    "event": "vector_upsert",
                    "id": item["id"],
                    "backend": self.vector_index.backend_name,
                    "embedding_provider": self.vector_index.embedding_provider_name,
                    "updated_at": item.get("updated_at", ""),
                },
            )
        except Exception as exc:
            append_jsonl(
                self.paths.knowledge_audit_log,
                {
                    "event": "vector_upsert_failed",
                    "id": item.get("id", ""),
                    "backend": self.vector_index.backend_name,
                    "error": str(exc),
                    "created_at": utc_now(),
                },
            )

    def _fetch_items_by_ids(self, ids: Sequence[str]) -> List[Dict[str, object]]:
        """Fetch conclusion items by id."""
        normalized = [str(item) for item in ids if str(item).strip()]
        if not normalized:
            return []
        placeholders = ", ".join("?" for _ in normalized)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM conclusions WHERE id IN (%s)" % placeholders,
                normalized,
            ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def _fetch_recent_items(self, limit: int = 200, project_slug: Optional[str] = None) -> List[Dict[str, object]]:
        """Fetch recent conclusion items without ranking."""
        sql = "SELECT * FROM conclusions"
        params: List[object] = []
        if project_slug:
            sql += " WHERE project_slug = ?"
            params.append(project_slug)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._row_to_item(row) for row in rows]

    def _tags_match(self, item: Dict[str, object], tags: Optional[Sequence[str]]) -> bool:
        """Return True when an item has all requested tags."""
        required = {str(tag).strip().lower() for tag in list(tags or []) if str(tag).strip()}
        if not required:
            return True
        existing = {str(tag).strip().lower() for tag in list(item.get("tags") or []) if str(tag).strip()}
        return required.issubset(existing)

    def _maybe_backfill_vector_index(self) -> None:
        """Backfill vector rows for pre-existing conclusions when the index is empty."""
        try:
            if not self.vector_index.enabled or self.vector_index.count() > 0:
                return
            for item in self._fetch_recent_items(limit=10000):
                self._upsert_vector_payload(item)
        except Exception:
            return

    def rebuild_vector_index(self) -> int:
        """Rebuild the vector index from canonical SQLite conclusions."""
        count = 0
        for item in self._fetch_recent_items(limit=10000):
            self._upsert_vector_payload(item)
            count += 1
        return count

    def _rebuild_index(self) -> None:
        """Rebuild the lightweight knowledge index file."""
        rows = self.list_recent(limit=200)
        lines = ["# Moonshine Knowledge Index", ""]
        for item in rows:
            lines.append(
                "- [{title}](entries/{id}.md) - {statement} [status: {status}, project: {project}]".format(
                    id=item["id"],
                    title=item["title"],
                    statement=shorten(item["statement"], 120),
                    status=item["status"],
                    project=item["project_slug"],
                )
            )
        atomic_write(self.paths.knowledge_index_file, "\n".join(lines).rstrip() + "\n")

    def add_conclusion(
        self,
        *,
        title: str,
        statement: str,
        proof_sketch: str = "",
        status: str = "partial",
        project_slug: str = "general",
        tags: Optional[List[str]] = None,
        source_type: str = "manual",
        source_ref: str = "",
    ) -> str:
        """Persist a structured conclusion."""
        conclusion_id = "conclusion-%s" % uuid.uuid4().hex[:10]
        created_at = utc_now()
        tags = list(tags or [])
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO conclusions
                (id, project_slug, title, statement, proof_sketch, status, tags_json, source_type, source_ref, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conclusion_id,
                    project_slug,
                    title.strip(),
                    statement.strip(),
                    proof_sketch.strip(),
                    status.strip(),
                    json.dumps(tags, ensure_ascii=False),
                    source_type.strip(),
                    source_ref.strip(),
                    created_at,
                    created_at,
                ),
            )
            if self.fts_enabled:
                connection.execute(
                    """
                    INSERT INTO knowledge_fts (conclusion_id, title, statement, proof_sketch, tags)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (conclusion_id, title.strip(), statement.strip(), proof_sketch.strip(), " ".join(tags)),
                )
        atomic_write(
            self._entry_path(conclusion_id),
            self._entry_markdown(
                conclusion_id=conclusion_id,
                title=title,
                statement=statement,
                proof_sketch=proof_sketch,
                status=status,
                project_slug=project_slug,
                tags=tags,
                source_type=source_type,
                source_ref=source_ref,
                created_at=created_at,
            ),
        )
        self._upsert_vector_payload(
            {
                "id": conclusion_id,
                "project_slug": project_slug,
                "title": title.strip(),
                "statement": statement.strip(),
                "proof_sketch": proof_sketch.strip(),
                "status": status.strip(),
                "tags": tags,
                "source_type": source_type.strip(),
                "source_ref": source_ref.strip(),
                "created_at": created_at,
                "updated_at": created_at,
                "path": str(self._entry_path(conclusion_id)),
            }
        )
        self._rebuild_index()

        append_jsonl(
            self.paths.knowledge_audit_log,
            {
                "event": "add_conclusion",
                "id": conclusion_id,
                "project_slug": project_slug,
                "title": title.strip(),
                "source_type": source_type.strip(),
                "source_ref": source_ref.strip(),
                "created_at": created_at,
            },
        )
        return conclusion_id

    def search(
        self,
        query: str,
        limit: int = 5,
        project_slug: Optional[str] = None,
        tags: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, object]]:
        """Search structured conclusions with hybrid FTS + vector retrieval."""
        query = str(query or "")
        if not query.strip():
            return [
                item
                for item in self._fetch_recent_items(limit=limit, project_slug=project_slug)
                if self._tags_match(item, tags)
            ][:limit]

        fts_scores: Dict[str, float] = {}
        fts_items: List[Dict[str, object]] = []
        with self._connect() as connection:
            if self.fts_enabled:
                tokens = tokenize(query)
                if tokens:
                    sql = """
                        SELECT c.*
                        FROM knowledge_fts f
                        JOIN conclusions c ON c.id = f.conclusion_id
                        WHERE knowledge_fts MATCH ?
                    """
                    params: List[object] = [" OR ".join(tokens)]
                    if project_slug:
                        sql += " AND c.project_slug = ?"
                        params.append(project_slug)
                    sql += " LIMIT ?"
                    params.append(limit * 8)
                    rows = connection.execute(sql, params).fetchall()
                    for rank, row in enumerate(rows):
                        item = self._row_to_item(row)
                        if not self._tags_match(item, tags):
                            continue
                        fts_items.append(item)
                        fts_scores[item["id"]] = max(fts_scores.get(item["id"], 0.0), 1.0 / float(rank + 1))

        self._maybe_backfill_vector_index()
        vector_hits = []
        try:
            vector_hits = self.vector_index.search(
                query,
                limit=limit * 8,
                project_slug=project_slug,
                tags=tags,
            )
        except Exception as exc:
            append_jsonl(
                self.paths.knowledge_audit_log,
                {
                    "event": "vector_search_failed",
                    "query": shorten(query, 160),
                    "backend": self.vector_index.backend_name,
                    "error": str(exc),
                    "created_at": utc_now(),
                },
            )

        vector_scores = {hit.conclusion_id: float(hit.score) for hit in vector_hits}
        vector_meta = {hit.conclusion_id: hit for hit in vector_hits}
        candidate_ids = list(dict.fromkeys([item["id"] for item in fts_items] + [hit.conclusion_id for hit in vector_hits]))
        if candidate_ids:
            candidates = self._fetch_items_by_ids(candidate_ids)
        else:
            candidates = self._fetch_recent_items(limit=200, project_slug=project_slug)
            candidates = [item for item in candidates if self._tags_match(item, tags)]

        vector_weight = float(getattr(self.config, "knowledge_vector_weight", 0.55) if self.config is not None else 0.55)
        fts_weight = float(getattr(self.config, "knowledge_fts_weight", 0.35) if self.config is not None else 0.35)
        lexical_weight = float(getattr(self.config, "knowledge_lexical_weight", 0.10) if self.config is not None else 0.10)

        ranked = []
        for item in candidates:
            if project_slug and item.get("project_slug") != project_slug:
                continue
            if not self._tags_match(item, tags):
                continue
            blob = " ".join([item["title"], item["statement"], item["proof_sketch"], " ".join(item["tags"])])
            lexical_score = overlap_score(query, blob)
            fts_score = fts_scores.get(item["id"], 0.0)
            vector_score = vector_scores.get(item["id"], 0.0)
            score = (vector_weight * vector_score) + (fts_weight * fts_score) + (lexical_weight * lexical_score)
            if score <= 0:
                continue
            hit = vector_meta.get(item["id"])
            item["retrieval"] = {
                "hybrid_score": score,
                "fts_score": fts_score,
                "lexical_score": lexical_score,
                "vector_score": vector_score,
                "vector_backend": hit.backend if hit is not None else self.vector_index.backend_name,
                "embedding_provider": (
                    hit.metadata.get("embedding_provider", self.vector_index.embedding_provider_name)
                    if hit is not None
                    else self.vector_index.embedding_provider_name
                ),
            }
            ranked.append((score, item))

        ranked.sort(key=lambda pair: (pair[0], pair[1]["updated_at"]), reverse=True)
        return [pair[1] for pair in ranked[:limit]]

    def list_recent(self, limit: int = 10, project_slug: Optional[str] = None) -> List[Dict[str, object]]:
        """Return recent conclusions."""
        return self.search("", limit=limit, project_slug=project_slug)
