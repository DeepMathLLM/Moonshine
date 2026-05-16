"""SQLite-backed research retrieval index for project-local research memory."""

from __future__ import annotations

import json
import re
import sqlite3
from hashlib import sha1
from typing import Dict, Iterable, List, Optional, Sequence

from moonshine.moonshine_constants import RESEARCH_MEMORY_CHANNELS
from moonshine.utils import (
    estimate_tokens_rough,
    overlap_score,
    parse_utc_timestamp,
    read_jsonl,
    read_text,
    shorten,
    tokenize,
    utc_now,
)


WORKSPACE_DOCS = {
    "problem": ("problem", "Current Problem", "problem.md"),
    "blueprint": ("blueprint", "Readable Research Log", "blueprint.md"),
    "verified": ("verified", "Verified Proof Blueprint", "blueprint_verified.md"),
}


def stable_claim_hash(text: str) -> str:
    """Return a stable hash for a mathematical claim or branch target."""
    normalized = re.sub(r"\s+", " ", str(text or "").strip().lower())
    if not normalized:
        return ""
    return sha1(normalized.encode("utf-8")).hexdigest()[:16]


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _safe_json_loads(text: str, default: object) -> object:
    try:
        return json.loads(text or "")
    except Exception:
        return default


def _safe_relative(paths, path) -> str:
    try:
        return path.relative_to(paths.home).as_posix()
    except ValueError:
        return path.as_posix()


def _hash_text(text: str) -> str:
    return sha1(str(text or "").encode("utf-8")).hexdigest()[:16]


def _best_exact_slice(text: str, query: str, *, char_budget: int = 1800) -> str:
    """Return a deterministic local slice around the strongest lexical match."""
    source = str(text or "").strip()
    if not source:
        return ""
    budget = max(240, int(char_budget or 1800))
    if len(source) <= budget:
        return source
    lowered = source.lower()
    normalized_query = str(query or "").strip().lower()
    index = -1
    if normalized_query:
        index = lowered.find(normalized_query)
    if index < 0:
        tokens = [token.lower() for token in tokenize(query) if len(token) >= 3]
        best_score = -1.0
        best_index = -1
        for token in tokens[:16]:
            pos = lowered.find(token)
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


class ResearchIndexStore(object):
    """Build and query one SQLite index per research project."""

    def __init__(self, paths):
        self.paths = paths

    def index_path(self, project_slug: str):
        return self.paths.project_research_index_file(project_slug)

    def _connect(self, project_slug: str):
        path = self.index_path(project_slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self, conn) -> bool:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_docs (
                key TEXT PRIMARY KEY,
                project_slug TEXT,
                source_type TEXT,
                source_id TEXT,
                source_path TEXT,
                title TEXT,
                summary TEXT,
                body TEXT,
                stage TEXT,
                focus_activity TEXT,
                channel TEXT,
                artifact_type TEXT,
                branch_id TEXT,
                claim TEXT,
                claim_hash TEXT,
                status TEXT,
                review_status TEXT,
                tags_json TEXT,
                metadata_json TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_research_docs_project ON research_docs(project_slug)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_research_docs_channel ON research_docs(channel)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_research_docs_claim_hash ON research_docs(claim_hash)")
        fts_available = True
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS research_docs_fts
                USING fts5(key UNINDEXED, title, summary, body, tags)
                """
            )
        except sqlite3.OperationalError:
            fts_available = False
        conn.commit()
        return fts_available

    def _upsert_document(self, conn, doc: Dict[str, object], *, fts_available: bool) -> None:
        tags = [str(item) for item in list(doc.get("tags") or []) if str(item).strip()]
        metadata = dict(doc.get("metadata") or {})
        key = str(doc.get("key") or "").strip()
        if not key:
            return
        conn.execute(
            """
            INSERT OR REPLACE INTO research_docs (
                key, project_slug, source_type, source_id, source_path, title, summary,
                body, stage, focus_activity, channel, artifact_type, branch_id, claim,
                claim_hash, status, review_status, tags_json, metadata_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                str(doc.get("project_slug") or ""),
                str(doc.get("source_type") or ""),
                str(doc.get("source_id") or ""),
                str(doc.get("source_path") or ""),
                str(doc.get("title") or ""),
                str(doc.get("summary") or ""),
                str(doc.get("body") or ""),
                str(doc.get("stage") or ""),
                str(doc.get("focus_activity") or ""),
                str(doc.get("channel") or ""),
                str(doc.get("artifact_type") or ""),
                str(doc.get("branch_id") or ""),
                str(doc.get("claim") or ""),
                str(doc.get("claim_hash") or stable_claim_hash(str(doc.get("claim") or ""))),
                str(doc.get("status") or ""),
                str(doc.get("review_status") or ""),
                _json_dumps(tags),
                _json_dumps(metadata),
                str(doc.get("created_at") or ""),
                str(doc.get("updated_at") or doc.get("created_at") or ""),
            ),
        )
        if fts_available:
            conn.execute("DELETE FROM research_docs_fts WHERE key = ?", (key,))
            conn.execute(
                "INSERT INTO research_docs_fts(key, title, summary, body, tags) VALUES (?, ?, ?, ?, ?)",
                (
                    key,
                    str(doc.get("title") or ""),
                    str(doc.get("summary") or ""),
                    str(doc.get("body") or ""),
                    " ".join(tags),
                ),
            )

    def _workspace_documents(self, project_slug: str) -> Iterable[Dict[str, object]]:
        paths_by_kind = {
            "problem": self.paths.project_problem_draft_file(project_slug),
            "blueprint": self.paths.project_blueprint_file(project_slug),
            "verified": self.paths.project_blueprint_verified_file(project_slug),
        }
        for kind, path in paths_by_kind.items():
            text = read_text(path).strip()
            if not text:
                continue
            _, title, filename = WORKSPACE_DOCS[kind]
            yield {
                "key": "workspace:%s" % kind,
                "project_slug": project_slug,
                "source_type": "workspace",
                "source_id": kind,
                "source_path": _safe_relative(self.paths, path),
                "title": title,
                "summary": shorten(text, 320),
                "body": text,
                "channel": "workspace",
                "artifact_type": "workspace_%s" % kind,
                "tags": ["canonical-workspace", kind, filename],
                "metadata": {"workspace_kind": kind, "filename": filename},
                "created_at": "",
                "updated_at": utc_now(),
            }

    def _research_log_documents(self, project_slug: str) -> Iterable[Dict[str, object]]:
        log_path = self.paths.project_research_log_file(project_slug)
        for item in read_jsonl(log_path):
            if not isinstance(item, dict):
                continue
            record_id = str(item.get("id") or "").strip()
            if not record_id:
                record_id = "research-log-%s" % _hash_text(_json_dumps(item))
            record_type = str(item.get("type") or "research_note")
            body = str(item.get("content") or "")
            title = str(item.get("title") or "").strip() or "Research Record"
            yield {
                "key": "research-log:%s" % record_id,
                "project_slug": project_slug,
                "source_type": "research_log",
                "source_id": record_id,
                "source_path": _safe_relative(self.paths, log_path),
                "title": title,
                "summary": shorten(body, 320),
                "body": body,
                "stage": "",
                "focus_activity": "",
                "channel": record_type,
                "artifact_type": record_type,
                "branch_id": "",
                "claim": "",
                "claim_hash": "",
                "status": "",
                "review_status": "",
                "tags": [record_type],
                "metadata": {
                    "record_type": record_type,
                    "source_refs": list(item.get("source_refs") or []),
                },
                "created_at": str(item.get("created_at") or ""),
                "updated_at": str(item.get("created_at") or ""),
            }

    def _verification_documents(self, project_slug: str) -> Iterable[Dict[str, object]]:
        verification_path = self.paths.project_research_verification_file(project_slug)
        for item in read_jsonl(verification_path):
            if not isinstance(item, dict):
                continue
            entry_id = str(item.get("id") or "").strip()
            if not entry_id:
                entry_id = "verification-%s" % _hash_text(_json_dumps(item))
            body = "\n".join(
                part
                for part in [
                    str(item.get("claim") or ""),
                    str(item.get("summary") or ""),
                    " ".join(str(x) for x in list(item.get("critical_errors") or [])),
                    " ".join(str(x) for x in list(item.get("gaps") or [])),
                ]
                if part
            )
            claim = str(item.get("claim") or "")
            yield {
                "key": "verification:%s" % entry_id,
                "project_slug": project_slug,
                "source_type": "verification",
                "source_id": entry_id,
                "source_path": _safe_relative(self.paths, verification_path),
                "title": "Verification: %s" % shorten(claim or str(item.get("summary") or ""), 120),
                "summary": str(item.get("summary") or ""),
                "body": body,
                "stage": str(item.get("stage") or "problem_solving"),
                "focus_activity": str(item.get("focus_activity") or "pessimistic_verification"),
                "channel": "verification_reports",
                "artifact_type": "verification_report",
                "branch_id": str(item.get("branch_id") or ""),
                "claim": claim,
                "claim_hash": str(item.get("claim_hash") or stable_claim_hash(claim)),
                "status": str(item.get("status") or ""),
                "review_status": str(item.get("review_status") or ""),
                "tags": ["verification"],
                "metadata": dict(item),
                "created_at": str(item.get("created_at") or item.get("reviewed_at") or ""),
                "updated_at": str(item.get("updated_at") or item.get("created_at") or item.get("reviewed_at") or ""),
            }

    def _iter_project_documents(self, project_slug: str) -> Iterable[Dict[str, object]]:
        yield from self._workspace_documents(project_slug)
        yield from self._research_log_documents(project_slug)
        yield from self._verification_documents(project_slug)

    def rebuild_project(self, project_slug: str) -> Dict[str, object]:
        """Rebuild one project index from canonical workspace plus durable logs."""
        conn = self._connect(project_slug)
        try:
            fts_available = self._ensure_schema(conn)
            conn.execute("DELETE FROM research_docs")
            if fts_available:
                conn.execute("DELETE FROM research_docs_fts")
            count = 0
            for doc in self._iter_project_documents(project_slug):
                self._upsert_document(conn, doc, fts_available=fts_available)
                count += 1
            conn.commit()
            return {"project_slug": project_slug, "indexed": count, "fts": fts_available}
        finally:
            conn.close()

    def _projects_for_search(self, project_slug: Optional[str]) -> List[str]:
        if project_slug:
            return [str(project_slug)]
        if not self.paths.projects_dir.exists():
            return []
        return [item.name for item in sorted(self.paths.projects_dir.iterdir()) if item.is_dir()]

    def _coerce_row(self, row, *, query: str, score: float) -> Dict[str, object]:
        metadata = dict(_safe_json_loads(row["metadata_json"], {}))
        tags = [str(item) for item in list(_safe_json_loads(row["tags_json"], [])) if str(item).strip()]
        body = str(row["body"] or "")
        exact_excerpt = _best_exact_slice(body or str(row["summary"] or ""), query)
        raw_text = body if estimate_tokens_rough(body) <= 1400 else ""
        metadata.update(
            {
                "retrieval_mode": "research_index",
                "source_type": str(row["source_type"] or ""),
                "source_path": str(row["source_path"] or ""),
                "exact_excerpt": exact_excerpt,
                "raw_text": raw_text,
                "claim_hash": str(row["claim_hash"] or ""),
                "branch_id": str(row["branch_id"] or ""),
                "channel": str(row["channel"] or ""),
            }
        )
        return {
            "id": str(row["source_id"] or row["key"] or ""),
            "key": str(row["key"] or ""),
            "artifact_type": str(row["artifact_type"] or row["source_type"] or "artifact"),
            "title": str(row["title"] or ""),
            "summary": str(row["summary"] or ""),
            "content_inline": exact_excerpt,
            "content_path": str(row["source_path"] or ""),
            "stage": str(row["stage"] or ""),
            "focus_activity": str(row["focus_activity"] or ""),
            "status": str(row["status"] or ""),
            "review_status": str(row["review_status"] or ""),
            "project_slug": str(row["project_slug"] or ""),
            "session_id": str(metadata.get("session_id") or ""),
            "tags": tags,
            "related_ids": list(metadata.get("related_ids") or []),
            "next_action": str(metadata.get("next_action") or ""),
            "metadata": metadata,
            "created_at": str(row["created_at"] or ""),
            "score": float(score or 0.0),
            "channel": str(row["channel"] or ""),
            "retrieval_mode": "research_index",
            "source_type": str(row["source_type"] or ""),
            "claim_hash": str(row["claim_hash"] or ""),
            "branch_id": str(row["branch_id"] or ""),
            "exact_excerpt": exact_excerpt,
        }

    def _fallback_rank(self, rows: Sequence[sqlite3.Row], query: str, *, limit: int) -> List[Dict[str, object]]:
        ranked = []
        for row in rows:
            blob = "\n".join(
                [
                    str(row["title"] or ""),
                    str(row["summary"] or ""),
                    str(row["body"] or ""),
                    str(row["tags_json"] or ""),
                    str(row["claim"] or ""),
                ]
            )
            score = overlap_score(query, blob) if str(query or "").strip() else 1.0
            if score <= 0.0 and str(query or "").strip():
                continue
            ranked.append(self._coerce_row(row, query=query, score=score))
        ranked.sort(
            key=lambda item: (
                -float(item.get("score") or 0.0),
                parse_utc_timestamp(str(item.get("created_at") or "")) or parse_utc_timestamp("1970-01-01T00:00:00Z"),
            ),
        )
        return ranked[: max(1, int(limit or 5))]

    def _search_one_project(
        self,
        project_slug: str,
        *,
        query: str,
        channels: Sequence[str],
        channel_mode: str,
        limit: int,
    ) -> List[Dict[str, object]]:
        self.rebuild_project(project_slug)
        conn = self._connect(project_slug)
        try:
            fts_available = self._ensure_schema(conn)
            filters = ["project_slug = ?"]
            params: List[object] = [project_slug]
            selected_channels = [str(channel) for channel in list(channels or []) if str(channel).strip()]
            if selected_channels:
                placeholders = ", ".join("?" for _ in selected_channels)
                filters.append("channel IN (%s)" % placeholders)
                params.extend(selected_channels)
            where = " AND ".join(filters)
            mode = str(channel_mode or "search").strip().lower()
            if mode in {"recent", "all"} or not str(query or "").strip():
                rows = conn.execute(
                    "SELECT * FROM research_docs WHERE %s ORDER BY COALESCE(created_at, updated_at) DESC" % where,
                    params,
                ).fetchall()
                return [self._coerce_row(row, query=query, score=1.0) for row in rows[: max(1, int(limit or 5))]]

            if fts_available:
                tokens = [re.sub(r"[^A-Za-z0-9_]+", "", token) for token in tokenize(query)]
                tokens = [token for token in tokens if token]
                if tokens:
                    fts_query = " OR ".join(tokens[:16])
                    try:
                        rows = conn.execute(
                            """
                            SELECT d.*, bm25(research_docs_fts) AS rank_score
                            FROM research_docs_fts
                            JOIN research_docs d ON d.key = research_docs_fts.key
                            WHERE research_docs_fts MATCH ?
                              AND %s
                            ORDER BY rank_score ASC
                            LIMIT ?
                            """
                            % where,
                            [fts_query] + params + [max(1, int(limit or 5))],
                        ).fetchall()
                        if rows:
                            return [
                                self._coerce_row(row, query=query, score=max(0.0001, 1.0 / float(index + 1)))
                                for index, row in enumerate(rows)
                            ]
                    except sqlite3.OperationalError:
                        pass
            rows = conn.execute("SELECT * FROM research_docs WHERE %s" % where, params).fetchall()
            return self._fallback_rank(rows, query, limit=limit)
        finally:
            conn.close()

    def search(
        self,
        *,
        query: str,
        project_slug: Optional[str],
        channels: Optional[Sequence[str]] = None,
        channel_mode: str = "search",
        limit: int = 5,
        limit_per_channel: int = 3,
    ) -> List[Dict[str, object]]:
        """Search research memory through the SQLite index."""
        selected_channels = [str(channel) for channel in list(channels or []) if str(channel).strip()]
        per_project_limit = max(1, int(limit_per_channel if selected_channels else limit or 5))
        rows: List[Dict[str, object]] = []
        for slug in self._projects_for_search(project_slug):
            rows.extend(
                self._search_one_project(
                    slug,
                    query=query,
                    channels=selected_channels,
                    channel_mode=channel_mode,
                    limit=per_project_limit,
                )
            )
        if str(channel_mode or "search").strip().lower() == "search":
            rows.sort(
                key=lambda item: (
                    -float(item.get("score") or 0.0),
                    parse_utc_timestamp(str(item.get("created_at") or "")) or parse_utc_timestamp("1970-01-01T00:00:00Z"),
                )
            )
        else:
            rows.sort(
                key=lambda item: parse_utc_timestamp(str(item.get("created_at") or "")) or parse_utc_timestamp("1970-01-01T00:00:00Z"),
                reverse=True,
            )
        hard_limit = max(1, int(limit_per_channel if selected_channels else limit or 5))
        if selected_channels:
            hard_limit *= max(1, len(selected_channels))
        return rows[:hard_limit]
