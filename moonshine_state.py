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
