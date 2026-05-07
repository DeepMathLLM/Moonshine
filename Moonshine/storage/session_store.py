"""High-level session storage with SQLite and trace files."""

from __future__ import annotations

import gzip
import json
import uuid
from typing import Dict, List, Optional

from moonshine.moonshine_constants import MoonshinePaths
from moonshine.moonshine_state import SessionStateDB
from moonshine.utils import append_jsonl, ensure_directory, read_json, read_jsonl, shorten, utc_now, write_json


class SessionStore(object):
    """Persist sessions to SQLite and a traceable file tree."""

    def __init__(self, paths: MoonshinePaths):
        self.paths = paths
        self.db = SessionStateDB(paths.sessions_db)

    def create_session(self, mode: str, project_slug: str, agent_slug: str = "") -> str:
        """Create a new session and its trace directory."""
        session_id = "session-%s" % uuid.uuid4().hex[:10]
        started_at = utc_now()
        ensure_directory(self.paths.session_dir(session_id))
        ensure_directory(self.paths.session_artifacts_dir(session_id))

        self.db.create_session(session_id, mode, project_slug, started_at)
        write_json(
            self.paths.session_meta_file(session_id),
            {
                "id": session_id,
                "mode": mode,
                "project_slug": project_slug,
                "agent_slug": str(agent_slug or ""),
                "started_at": started_at,
                "updated_at": started_at,
                "status": "active",
            },
        )
        if not self.paths.session_transcript_file(session_id).exists():
            self.paths.session_transcript_file(session_id).write_text(
                "# Session %s\n\n- Mode: %s\n- Project: %s\n- Agent: %s\n- Started: %s\n\n"
                % (session_id, mode, project_slug, str(agent_slug or ""), started_at),
                encoding="utf-8",
            )
        return session_id

    def append_message(self, session_id: str, role: str, content: str, metadata: Optional[Dict[str, object]] = None) -> int:
        """Append a message to both SQLite and session files."""
        created_at = utc_now()
        metadata = dict(metadata or {})
        message_id = self.db.insert_message(session_id, role, content, json.dumps(metadata, ensure_ascii=False), created_at)
        self.db.insert_conversation_event(
            session_id=session_id,
            message_id=message_id,
            event_kind="message",
            role=role,
            content=content,
            payload_json=json.dumps({"metadata": metadata}, ensure_ascii=False),
            created_at=created_at,
        )

        append_jsonl(
            self.paths.session_messages_file(session_id),
            {
                "id": message_id,
                "session_id": session_id,
                "role": role,
                "content": content,
                "metadata": metadata,
                "created_at": created_at,
            },
        )
        with self.paths.session_transcript_file(session_id).open("a", encoding="utf-8") as handle:
            handle.write("## %s\n\n%s\n\n" % (role.capitalize(), content.strip()))

        session_meta = read_json(self.paths.session_meta_file(session_id), default={}) or {}
        if not session_meta.get("title") and role == "user":
            session_meta["title"] = shorten(content, 72)
        session_meta["updated_at"] = created_at
        session_meta["last_message_id"] = message_id
        write_json(self.paths.session_meta_file(session_id), session_meta)
        self.db.update_session(session_id, title=session_meta.get("title"), updated_at=created_at)
        return message_id

    def append_conversation_event(
        self,
        session_id: str,
        *,
        event_kind: str,
        role: str,
        content: str,
        payload: Optional[Dict[str, object]] = None,
        message_id: Optional[int] = None,
        created_at: Optional[str] = None,
    ) -> int:
        """Append a structured internal conversation event to SQLite."""
        return self.db.insert_conversation_event(
            session_id=session_id,
            message_id=message_id,
            event_kind=event_kind,
            role=role,
            content=content,
            payload_json=json.dumps(dict(payload or {}), ensure_ascii=False),
            created_at=created_at or utc_now(),
        )

    def _render_tool_event_content(self, payload: Dict[str, object]) -> str:
        """Render a tool event as compact text for search and local windows."""
        parts = [
            "Tool: %s" % payload.get("tool", ""),
            "Arguments: %s" % json.dumps(payload.get("arguments", {}), ensure_ascii=False),
            "Output: %s" % json.dumps(payload.get("output", {}), ensure_ascii=False),
        ]
        if payload.get("error"):
            parts.append("Error: %s" % payload.get("error"))
        return "\n".join(parts)

    def append_tool_event(self, session_id: str, payload: Dict[str, object]) -> None:
        """Record a tool event for traceability."""
        self.db.insert_conversation_event(
            session_id=session_id,
            event_kind="tool_result",
            role="tool",
            content=self._render_tool_event_content(payload),
            payload_json=json.dumps(payload, ensure_ascii=False),
            created_at=str(payload.get("created_at") or utc_now()),
        )
        append_jsonl(self.paths.session_tool_events_file(session_id), payload)

    def append_turn_event(self, session_id: str, payload: Dict[str, object]) -> None:
        """Record an agent-loop event for traceability."""
        append_jsonl(self.paths.session_turn_events_file(session_id), payload)

    def append_provider_round(self, session_id: str, payload: Dict[str, object]) -> None:
        """Record one provider request/response snapshot without growing a live markdown trace."""
        archive_dir = self.paths.session_provider_round_archives_dir(session_id)
        ensure_directory(archive_dir)
        round_id = str(payload.get("id") or "round-%s" % uuid.uuid4().hex[:12])
        archive_path = archive_dir / ("%s.json.gz" % round_id)
        with gzip.open(archive_path, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.write("\n")

        response = dict(payload.get("response") or {})
        compact = {
            "id": round_id,
            "created_at": payload.get("created_at", ""),
            "phase": payload.get("phase", "main"),
            "title": payload.get("title", "Provider Round"),
            "model_round": payload.get("model_round", ""),
            "tool_schema_names": list(payload.get("tool_schema_names") or []),
            "message_count": len(list(payload.get("messages") or [])),
            "response_content_preview": shorten(str(response.get("content") or ""), 240),
            "tool_call_names": [
                str(item.get("name") or "")
                for item in list(response.get("tool_calls") or [])
                if isinstance(item, dict) and str(item.get("name") or "")
            ],
            "archive_path": str(archive_path.relative_to(self.paths.home).as_posix()),
            "archive_format": "json.gz",
        }
        append_jsonl(self.paths.session_provider_rounds_file(session_id), compact)

    def _read_provider_round_archive(self, archive_path: str) -> Dict[str, object]:
        """Read a gzip provider-round archive, returning an empty dict on failure."""
        path = self.paths.home / str(archive_path or "")
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            return dict(payload or {}) if isinstance(payload, dict) else {}
        except (OSError, ValueError):
            return {}

    def get_recent_messages(self, session_id: str, limit: int = 8) -> List[Dict[str, object]]:
        """Return recent messages in chronological order."""
        rows = list(self.db.fetch_recent_messages(session_id, limit))
        rows.reverse()
        return [
            {
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_all_messages(self, session_id: str) -> List[Dict[str, object]]:
        """Return all messages in chronological order."""
        return [
            {
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
            }
            for row in self.db.fetch_all_messages(session_id)
        ]

    def get_message_window(self, session_id: str, anchor_message_id: int, before: int = 2, after: int = 2) -> List[Dict[str, object]]:
        """Return a small chronological message window around an anchor message."""
        messages = self.get_all_messages(session_id)
        anchor_index = 0
        for index, item in enumerate(messages):
            if int(item["id"]) == int(anchor_message_id):
                anchor_index = index
                break
        start = max(0, anchor_index - max(0, int(before)))
        end = min(len(messages), anchor_index + max(0, int(after)) + 1)
        return messages[start:end]

    def get_tool_events(self, session_id: str) -> List[Dict[str, object]]:
        """Return tool events for one session."""
        return [dict(item) for item in read_jsonl(self.paths.session_tool_events_file(session_id))]

    def get_provider_rounds(self, session_id: str) -> List[Dict[str, object]]:
        """Return provider request/response rounds for one session."""
        rows = []
        for item in read_jsonl(self.paths.session_provider_rounds_file(session_id)):
            if not isinstance(item, dict):
                continue
            archive_path = str(item.get("archive_path") or "")
            if archive_path:
                archived = self._read_provider_round_archive(archive_path)
                if archived:
                    archived.setdefault("archive_path", archive_path)
                    rows.append(archived)
                    continue
            rows.append(dict(item))
        return rows

    def get_conversation_events(self, session_id: str) -> List[Dict[str, object]]:
        """Return all structured conversation events for one session."""
        def _payload(value: str) -> Dict[str, object]:
            try:
                return json.loads(value)
            except ValueError:
                return {}

        return [
            {
                "id": row["id"],
                "message_id": row["message_id"],
                "event_kind": row["event_kind"],
                "role": row["role"],
                "content": row["content"],
                "payload": _payload(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in self.db.fetch_all_conversation_events(session_id)
        ]

    def get_conversation_window(
        self,
        session_id: str,
        *,
        anchor_message_id: Optional[int] = None,
        anchor_event_id: Optional[int] = None,
        before: int = 4,
        after: int = 6,
    ) -> List[Dict[str, object]]:
        """Return a local conversation-event window around one message anchor."""
        events = self.get_conversation_events(session_id)
        if not events:
            return []
        anchor_index = len(events) - 1
        if anchor_event_id is not None:
            for index, item in enumerate(events):
                if int(item["id"]) == int(anchor_event_id):
                    anchor_index = index
                    break
        elif anchor_message_id is not None:
            for index, item in enumerate(events):
                if item.get("message_id") is not None and int(item["message_id"]) == int(anchor_message_id):
                    anchor_index = index
                    break
        start = max(0, anchor_index - max(0, int(before)))
        end = min(len(events), anchor_index + max(0, int(after)) + 1)
        return events[start:end]

    def get_session_meta(self, session_id: str) -> Dict[str, object]:
        """Read the session metadata file."""
        return read_json(self.paths.session_meta_file(session_id), default={}) or {}

    def update_session_meta(self, session_id: str, **changes: object) -> Dict[str, object]:
        """Update session metadata fields."""
        payload = self.get_session_meta(session_id)
        payload.update(changes)
        write_json(self.paths.session_meta_file(session_id), payload)
        return payload

    def rebind_session_project(self, session_id: str, project_slug: str) -> Dict[str, object]:
        """Update a session's project scope after automatic research project resolution."""
        updated_at = utc_now()
        payload = self.update_session_meta(session_id, project_slug=project_slug, updated_at=updated_at)
        self.db.update_session_project(session_id, project_slug, updated_at)
        transcript = self.paths.session_transcript_file(session_id)
        if transcript.exists():
            with transcript.open("a", encoding="utf-8") as handle:
                handle.write("## System\n\nResearch project resolved: `%s`\n\n" % project_slug)
        return payload

    def list_sessions(self, limit: int = 10) -> List[Dict[str, object]]:
        """List recent sessions."""
        return [dict(row) for row in self.db.fetch_sessions(limit)]

    def search_messages(self, query: str, limit: int = 5, project_slug: Optional[str] = None) -> List[Dict[str, object]]:
        """Search messages across sessions."""
        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "role": row["role"],
                "content": row["content"],
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
                "project_slug": row["project_slug"],
            }
            for row in self.db.search_messages(query, limit, project_slug=project_slug)
        ]

    def search_conversation_events(self, query: str, limit: int = 5, project_slug: Optional[str] = None) -> List[Dict[str, object]]:
        """Search structured conversation events across sessions."""
        def _payload(value: str) -> Dict[str, object]:
            try:
                return json.loads(value)
            except ValueError:
                return {}

        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "message_id": row["message_id"],
                "event_kind": row["event_kind"],
                "role": row["role"],
                "content": row["content"],
                "payload": _payload(row["payload_json"]),
                "created_at": row["created_at"],
                "project_slug": row["project_slug"],
            }
            for row in self.db.search_conversation_events(query, limit, project_slug=project_slug)
        ]

    def mark_closed(self, session_id: str) -> None:
        """Mark a session as closed."""
        updated_at = utc_now()
        session_meta = read_json(self.paths.session_meta_file(session_id), default={}) or {}
        session_meta["updated_at"] = updated_at
        session_meta["status"] = "closed"
        write_json(self.paths.session_meta_file(session_id), session_meta)
        self.db.update_session(session_id, updated_at=updated_at, status="closed")
