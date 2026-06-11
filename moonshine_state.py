"""SQLite-backed session state database for Moonshine."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import List, Optional

from moonshine.utils import overlap_score, tokenize


class SessionStateDB(object):
    """Low-level session database with an optional FTS5 index."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.fts_enabled = False
        self.events_fts_enabled = False
        self.tool_events_fts_enabled = False
        self.session_records_fts_enabled = False
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
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    project_slug TEXT NOT NULL,
                    title TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    message_id INTEGER,
                    event_kind TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_events_session
                ON conversation_events(session_id, id)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    call_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    archive_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    search_text TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_events_unique
                ON tool_events(session_id, event_id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tool_events_session
                ON tool_events(session_id, created_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS session_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    record_type TEXT NOT NULL,
                    role TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    search_text TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_session_records_unique
                ON session_records(session_id, record_id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_session_records_session
                ON session_records(session_id, id)
                """
            )
            try:
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                    USING fts5(message_id UNINDEXED, session_id UNINDEXED, role UNINDEXED, content)
                    """
                )
                self.fts_enabled = True
            except sqlite3.OperationalError:
                self.fts_enabled = False
            try:
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS conversation_events_fts
                    USING fts5(event_id UNINDEXED, session_id UNINDEXED, event_kind UNINDEXED, role UNINDEXED, content)
                    """
                )
                self.events_fts_enabled = True
            except sqlite3.OperationalError:
                self.events_fts_enabled = False
            try:
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS tool_events_fts
                    USING fts5(tool_event_id UNINDEXED, session_id UNINDEXED, tool UNINDEXED, content)
                    """
                )
                self.tool_events_fts_enabled = True
            except sqlite3.OperationalError:
                self.tool_events_fts_enabled = False
            try:
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS session_records_fts
                    USING fts5(record_row_id UNINDEXED, session_id UNINDEXED, record_type UNINDEXED, content)
                    """
                )
                self.session_records_fts_enabled = True
            except sqlite3.OperationalError:
                self.session_records_fts_enabled = False
            self._migrate_schema(connection)

    def _message_search_text(self, row) -> str:
        """Return searchable message text including assistant reasoning metadata."""
        content = str(row["content"] or "")
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except ValueError:
            metadata = {}
        reasoning_content = str(dict(metadata or {}).get("reasoning_content") or "").strip()
        if reasoning_content:
            return "%s\n\nReasoning content:\n%s" % (content, reasoning_content)
        return content

    def _event_search_text(self, row) -> str:
        """Return searchable event text including reasoning stored in payload JSON."""
        content = str(row["content"] or "")
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except ValueError:
            payload = {}
        payload_dict = dict(payload or {})
        metadata = dict(payload_dict.get("metadata") or {})
        message_payload = dict(payload_dict.get("message") or {})
        reasoning_content = str(metadata.get("reasoning_content") or message_payload.get("reasoning_content") or "").strip()
        if reasoning_content:
            return "%s\n\nReasoning content:\n%s" % (content, reasoning_content)
        return content

    def _migrate_schema(self, connection: sqlite3.Connection) -> None:
        """Patch older schemas in place."""
        session_columns = {row["name"] for row in connection.execute("PRAGMA table_info(sessions)").fetchall()}
        if "status" not in session_columns and session_columns:
            connection.execute("ALTER TABLE sessions ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")

        message_columns = {row["name"] for row in connection.execute("PRAGMA table_info(messages)").fetchall()}
        if "metadata_json" not in message_columns and message_columns:
            connection.execute("ALTER TABLE messages ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")

        event_columns = {row["name"] for row in connection.execute("PRAGMA table_info(conversation_events)").fetchall()}
        if event_columns and "message_id" not in event_columns:
            connection.execute("ALTER TABLE conversation_events ADD COLUMN message_id INTEGER")

    def create_session(self, session_id: str, mode: str, project_slug: str, started_at: str) -> None:
        """Create a new session record."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (id, mode, project_slug, title, started_at, updated_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, mode, project_slug, "", started_at, started_at, "active"),
            )

    def update_session(
        self,
        session_id: str,
        *,
        title: Optional[str] = None,
        updated_at: Optional[str] = None,
        status: Optional[str] = None,
    ) -> None:
        """Update mutable session fields."""
        assignments: List[str] = []
        values: List[object] = []
        if title is not None:
            assignments.append("title = ?")
            values.append(title)
        if updated_at is not None:
            assignments.append("updated_at = ?")
            values.append(updated_at)
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if not assignments:
            return
        values.append(session_id)
        with self._connect() as connection:
            connection.execute("UPDATE sessions SET %s WHERE id = ?" % ", ".join(assignments), values)

    def update_session_project(self, session_id: str, project_slug: str, updated_at: str) -> None:
        """Rebind a session to a project slug."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET project_slug = ?, updated_at = ?
                WHERE id = ?
                """,
                (project_slug, updated_at, session_id),
            )

    def insert_message(self, session_id: str, role: str, content: str, metadata_json: str, created_at: str) -> int:
        """Insert a message record and update FTS if available."""
        try:
            metadata = json.loads(metadata_json or "{}")
        except ValueError:
            metadata = {}
        search_content = content
        reasoning_content = str(dict(metadata or {}).get("reasoning_content") or "").strip()
        if reasoning_content:
            search_content = "%s\n\nReasoning content:\n%s" % (content, reasoning_content)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO messages (session_id, role, content, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, role, content, metadata_json, created_at),
            )
            message_id = int(cursor.lastrowid)
            if self.fts_enabled:
                connection.execute(
                    """
                    INSERT INTO messages_fts (message_id, session_id, role, content)
                    VALUES (?, ?, ?, ?)
                    """,
                    (message_id, session_id, role, search_content),
                )
        return message_id

    def insert_conversation_event(
        self,
        *,
        session_id: str,
        event_kind: str,
        role: str,
        content: str,
        payload_json: str,
        created_at: str,
        message_id: Optional[int] = None,
    ) -> int:
        """Insert a structured conversation event and update FTS if available."""
        try:
            payload = json.loads(payload_json or "{}")
        except ValueError:
            payload = {}
        search_content = content
        payload_dict = dict(payload or {})
        metadata = dict(payload_dict.get("metadata") or {})
        message_payload = dict(payload_dict.get("message") or {})
        reasoning_content = str(metadata.get("reasoning_content") or message_payload.get("reasoning_content") or "").strip()
        if reasoning_content:
            search_content = "%s\n\nReasoning content:\n%s" % (content, reasoning_content)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO conversation_events (session_id, message_id, event_kind, role, content, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, message_id, event_kind, role, content, payload_json, created_at),
            )
            event_id = int(cursor.lastrowid)
            if self.events_fts_enabled:
                connection.execute(
                    """
                    INSERT INTO conversation_events_fts (event_id, session_id, event_kind, role, content)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (event_id, session_id, event_kind, role, search_content),
                )
        return event_id

    def upsert_tool_event_index(
        self,
        *,
        session_id: str,
        event_id: str,
        tool: str,
        call_id: str,
        created_at: str,
        archive_path: str,
        status: str,
        search_text: str,
        metadata_json: str,
    ) -> int:
        """Insert or update one searchable tool event index record."""
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT id FROM tool_events
                WHERE session_id = ? AND event_id = ?
                """,
                (session_id, event_id),
            ).fetchone()
            if existing:
                row_id = int(existing["id"])
                connection.execute(
                    """
                    UPDATE tool_events
                    SET tool = ?, call_id = ?, created_at = ?, archive_path = ?,
                        status = ?, search_text = ?, metadata_json = ?
                    WHERE id = ?
                    """,
                    (tool, call_id, created_at, archive_path, status, search_text, metadata_json, row_id),
                )
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO tool_events
                    (session_id, event_id, tool, call_id, created_at, archive_path, status, search_text, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (session_id, event_id, tool, call_id, created_at, archive_path, status, search_text, metadata_json),
                )
                row_id = int(cursor.lastrowid)
            if self.tool_events_fts_enabled:
                connection.execute("DELETE FROM tool_events_fts WHERE tool_event_id = ?", (row_id,))
                connection.execute(
                    """
                    INSERT INTO tool_events_fts (tool_event_id, session_id, tool, content)
                    VALUES (?, ?, ?, ?)
                    """,
                    (row_id, session_id, tool, search_text),
                )
        return row_id

    def upsert_session_record(
        self,
        *,
        session_id: str,
        record_id: str,
        record_type: str,
        role: str,
        title: str,
        content: str,
        search_text: str,
        metadata_json: str,
        created_at: str,
    ) -> int:
        """Insert or update one unified searchable session record."""
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT id FROM session_records
                WHERE session_id = ? AND record_id = ?
                """,
                (session_id, record_id),
            ).fetchone()
            if existing:
                row_id = int(existing["id"])
                connection.execute(
                    """
                    UPDATE session_records
                    SET record_type = ?, role = ?, title = ?, content = ?,
                        search_text = ?, metadata_json = ?, created_at = ?
                    WHERE id = ?
                    """,
                    (record_type, role, title, content, search_text, metadata_json, created_at, row_id),
                )
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO session_records
                    (session_id, record_id, record_type, role, title, content, search_text, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (session_id, record_id, record_type, role, title, content, search_text, metadata_json, created_at),
                )
                row_id = int(cursor.lastrowid)
            if self.session_records_fts_enabled:
                connection.execute("DELETE FROM session_records_fts WHERE record_row_id = ?", (row_id,))
                connection.execute(
                    """
                    INSERT INTO session_records_fts (record_row_id, session_id, record_type, content)
                    VALUES (?, ?, ?, ?)
                    """,
                    (row_id, session_id, record_type, search_text),
                )
        return row_id

    def fetch_session_ids(self, project_slug: Optional[str] = None):
        """Return known session ids, optionally scoped to one project."""
        with self._connect() as connection:
            sql = "SELECT id FROM sessions"
            params: List[object] = []
            if project_slug:
                sql += " WHERE project_slug = ?"
                params.append(project_slug)
            sql += " ORDER BY updated_at DESC"
            return connection.execute(sql, params).fetchall()

    def fetch_session_record_index_records(self, session_id: str) -> dict:
        """Return session-record index metadata keyed by record id."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT record_id, metadata_json FROM session_records
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchall()
        records = {}
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except ValueError:
                metadata = {}
            records[str(row["record_id"])] = dict(metadata or {})
        return records

    def search_session_records(
        self,
        query: str,
        limit: int,
        *,
        project_slug: Optional[str] = None,
        session_id: Optional[str] = None,
    ):
        """Search the unified session record index."""
        if not query.strip():
            return []
        with self._connect() as connection:
            if self.session_records_fts_enabled:
                tokens = tokenize(query)
                if tokens:
                    sql = """
                        SELECT r.id, r.session_id, r.record_id, r.record_type,
                               r.role, r.title, r.content, r.search_text,
                               r.metadata_json, r.created_at, s.project_slug
                        FROM session_records_fts f
                        JOIN session_records r ON r.id = CAST(f.record_row_id AS INTEGER)
                        JOIN sessions s ON s.id = r.session_id
                        WHERE f.content MATCH ?
                    """
                    params: List[object] = [" OR ".join(tokens)]
                    if project_slug:
                        sql += " AND s.project_slug = ?"
                        params.append(project_slug)
                    if session_id:
                        sql += " AND r.session_id = ?"
                        params.append(session_id)
                    sql += " ORDER BY r.created_at DESC LIMIT ?"
                    params.append(limit * 4)
                    rows = connection.execute(sql, params).fetchall()
                    ranked = sorted(
                        rows,
                        key=lambda row: (overlap_score(query, row["search_text"]), row["created_at"], row["id"]),
                        reverse=True,
                    )
                    if ranked:
                        return ranked[:limit]

            sql = """
                SELECT r.id, r.session_id, r.record_id, r.record_type,
                       r.role, r.title, r.content, r.search_text,
                       r.metadata_json, r.created_at, s.project_slug
                FROM session_records r
                JOIN sessions s ON s.id = r.session_id
                WHERE (r.search_text LIKE ? OR r.title LIKE ? OR r.metadata_json LIKE ?)
            """
            params = ["%%%s%%" % query, "%%%s%%" % query, "%%%s%%" % query]
            if project_slug:
                sql += " AND s.project_slug = ?"
                params.append(project_slug)
            if session_id:
                sql += " AND r.session_id = ?"
                params.append(session_id)
            sql += " ORDER BY r.created_at DESC LIMIT ?"
            params.append(limit)
            return connection.execute(sql, params).fetchall()

    def fetch_all_session_records(self, session_id: str):
        """Return unified session records for one session."""
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT id, session_id, record_id, record_type, role, title,
                       content, search_text, metadata_json, created_at
                FROM session_records
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()

    def search_tool_events(self, session_id: str, query: str, limit: int):
        """Search indexed tool events for one session."""
        if not query.strip():
            return []
        with self._connect() as connection:
            if self.tool_events_fts_enabled:
                tokens = tokenize(query)
                if tokens:
                    sql = """
                        SELECT t.id, t.session_id, t.event_id, t.tool, t.call_id,
                               t.created_at, t.archive_path, t.status, t.search_text,
                               t.metadata_json
                        FROM tool_events_fts f
                        JOIN tool_events t ON t.id = CAST(f.tool_event_id AS INTEGER)
                        WHERE f.session_id = ? AND f.content MATCH ?
                    """
                    rows = connection.execute(sql, (session_id, " OR ".join(tokens))).fetchall()
                    ranked = sorted(
                        rows,
                        key=lambda row: (overlap_score(query, row["search_text"]), row["created_at"]),
                        reverse=True,
                    )
                    if ranked:
                        return ranked[:limit]

            like = "%%%s%%" % query
            rows = connection.execute(
                """
                SELECT id, session_id, event_id, tool, call_id, created_at,
                       archive_path, status, search_text, metadata_json
                FROM tool_events
                WHERE session_id = ? AND search_text LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (session_id, like, limit),
            ).fetchall()
            return rows

    def fetch_tool_event_index_records(self, session_id: str) -> dict:
        """Return tool-event index metadata keyed by event id."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, metadata_json FROM tool_events
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchall()
        records = {}
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except ValueError:
                metadata = {}
            records[str(row["event_id"])] = dict(metadata or {})
        return records

    def fetch_recent_messages(self, session_id: str, limit: int):
        """Return recent messages for a session."""
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT id, role, content, metadata_json, created_at
                FROM messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()

    def fetch_all_messages(self, session_id: str):
        """Return all messages for a session in chronological order."""
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT id, role, content, metadata_json, created_at
                FROM messages
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()

    def fetch_all_conversation_events(self, session_id: str):
        """Return all structured conversation events for a session in chronological order."""
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT id, message_id, event_kind, role, content, payload_json, created_at
                FROM conversation_events
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()

    def search_conversation_events(self, query: str, limit: int, project_slug: Optional[str] = None):
        """Search structured conversation events with FTS fallback."""
        if not query.strip():
            return []

        with self._connect() as connection:
            if self.events_fts_enabled:
                tokens = tokenize(query)
                if tokens:
                    sql = """
                        SELECT e.id, e.session_id, e.message_id, e.event_kind, e.role, e.content, e.payload_json, e.created_at, s.project_slug
                        FROM conversation_events_fts f
                        JOIN conversation_events e ON e.id = CAST(f.event_id AS INTEGER)
                        JOIN sessions s ON s.id = e.session_id
                        WHERE f.content MATCH ?
                    """
                    params: List[object] = [" OR ".join(tokens)]
                    if project_slug:
                        sql += " AND s.project_slug = ?"
                        params.append(project_slug)
                    sql += " ORDER BY e.created_at DESC LIMIT ?"
                    params.append(limit * 4)
                    rows = connection.execute(sql, params).fetchall()
                    ranked = sorted(
                        rows,
                        key=lambda row: (overlap_score(query, self._event_search_text(row)), row["created_at"]),
                        reverse=True,
                    )
                    if ranked:
                        return ranked[:limit]

            sql = """
                SELECT e.id, e.session_id, e.message_id, e.event_kind, e.role, e.content, e.payload_json, e.created_at, s.project_slug
                FROM conversation_events e
                JOIN sessions s ON s.id = e.session_id
                WHERE (e.content LIKE ? OR e.payload_json LIKE ?)
            """
            params: List[object] = ["%%%s%%" % query, "%%%s%%" % query]
            if project_slug:
                sql += " AND s.project_slug = ?"
                params.append(project_slug)
            sql += " ORDER BY e.created_at DESC LIMIT ?"
            params.append(limit)
            return connection.execute(sql, params).fetchall()

    def fetch_sessions(self, limit: int):
        """Return recent sessions."""
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT id, mode, project_slug, title, started_at, updated_at, status
                FROM sessions
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def search_messages(self, query: str, limit: int, project_slug: Optional[str] = None):
        """Search messages with FTS fallback."""
        if not query.strip():
            return []

        with self._connect() as connection:
            if self.fts_enabled:
                tokens = tokenize(query)
                if tokens:
                    sql = """
                        SELECT m.id, m.session_id, m.role, m.content, m.metadata_json, m.created_at, s.project_slug
                        FROM messages_fts f
                        JOIN messages m ON m.id = CAST(f.message_id AS INTEGER)
                        JOIN sessions s ON s.id = m.session_id
                        WHERE f.content MATCH ?
                    """
                    params: List[object] = [" OR ".join(tokens)]
                    if project_slug:
                        sql += " AND s.project_slug = ?"
                        params.append(project_slug)
                    sql += " ORDER BY m.created_at DESC LIMIT ?"
                    params.append(limit * 4)
                    rows = connection.execute(sql, params).fetchall()
                    ranked = sorted(
                        rows,
                        key=lambda row: (overlap_score(query, self._message_search_text(row)), row["created_at"]),
                        reverse=True,
                    )
                    if ranked:
                        return ranked[:limit]

            sql = """
                SELECT m.id, m.session_id, m.role, m.content, m.metadata_json, m.created_at, s.project_slug
                FROM messages m
                JOIN sessions s ON s.id = m.session_id
                WHERE (m.content LIKE ? OR m.metadata_json LIKE ?)
            """
            params: List[object] = ["%%%s%%" % query, "%%%s%%" % query]
            if project_slug:
                sql += " AND s.project_slug = ?"
                params.append(project_slug)
            sql += " ORDER BY m.created_at DESC LIMIT ?"
            params.append(limit)
            return connection.execute(sql, params).fetchall()
