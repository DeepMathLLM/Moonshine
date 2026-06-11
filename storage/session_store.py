"""High-level session storage with SQLite and trace files."""

from __future__ import annotations

import gzip
import hashlib
import json
import uuid
from typing import Dict, List, Optional

from moonshine.moonshine_constants import MoonshinePaths
from moonshine.moonshine_state import SessionStateDB
from moonshine.utils import append_jsonl, ensure_directory, overlap_score, read_json, read_jsonl, shorten, utc_now, write_json


RETRIEVAL_TOOL_EVENT_NAMES = {
    "list_mcp_servers",
    "list_sessions",
    "load_agent_definition",
    "load_mcp_server_definition",
    "load_skill_definition",
    "load_tool_definition",
    "memory_overview",
    "query_memory",
    "query_session_records",
    "search_knowledge",
    "search_sessions",
}

TOOL_EVENT_INDEX_VERSION = 2
SESSION_RECORD_INDEX_VERSION = 1


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
        self._upsert_message_session_record(
            session_id=session_id,
            message_id=message_id,
            role=role,
            content=content,
            metadata=metadata,
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
        event_created_at = created_at or utc_now()
        event_id = self.db.insert_conversation_event(
            session_id=session_id,
            message_id=message_id,
            event_kind=event_kind,
            role=role,
            content=content,
            payload_json=json.dumps(dict(payload or {}), ensure_ascii=False),
            created_at=event_created_at,
        )
        if event_kind not in {"message", "tool_result"}:
            self._upsert_event_session_record(
                session_id=session_id,
                event_id=event_id,
                event_kind=event_kind,
                role=role,
                content=content,
                payload=dict(payload or {}),
                message_id=message_id,
                created_at=event_created_at,
            )
        return event_id

    def _message_record_content(self, role: str, content: str, metadata: Dict[str, object]) -> str:
        """Render one message exactly enough for unified session retrieval."""
        reasoning_content = str(dict(metadata or {}).get("reasoning_content") or "").strip()
        if str(role or "") == "assistant" and reasoning_content:
            return json.dumps(
                {
                    "role": role,
                    "reasoning_content": reasoning_content,
                    "content": str(content or ""),
                },
                ensure_ascii=False,
            )
        return str(content or "")

    def _upsert_message_session_record(
        self,
        *,
        session_id: str,
        message_id: int,
        role: str,
        content: str,
        metadata: Dict[str, object],
        created_at: str,
    ) -> None:
        """Index one user/assistant message as a unified session record."""
        record_id = "msg:%s" % int(message_id)
        rendered = self._message_record_content(role, content, metadata)
        payload = {
            "index_version": SESSION_RECORD_INDEX_VERSION,
            "message_id": int(message_id),
            "source": "messages",
            "role": str(role or ""),
            "reasoning_content": str(dict(metadata or {}).get("reasoning_content") or ""),
        }
        self.db.upsert_session_record(
            session_id=session_id,
            record_id=record_id,
            record_type="message",
            role=str(role or ""),
            title="[%s] %s" % (str(role or "message"), shorten(str(content or ""), 96)),
            content=rendered,
            search_text=rendered,
            metadata_json=json.dumps(payload, ensure_ascii=False),
            created_at=created_at,
        )

    def _upsert_event_session_record(
        self,
        *,
        session_id: str,
        event_id: int,
        event_kind: str,
        role: str,
        content: str,
        payload: Dict[str, object],
        message_id: Optional[int],
        created_at: str,
    ) -> None:
        """Index non-message internal events once in the unified session record index."""
        payload = dict(payload or {})
        searchable = str(content or "")
        try:
            message_payload = dict(payload.get("message") or {})
        except (TypeError, ValueError):
            message_payload = {}
        if message_payload.get("tool_calls"):
            searchable = json.dumps(
                {
                    "event_kind": event_kind,
                    "role": role,
                    "content": content,
                    "tool_calls": message_payload.get("tool_calls") or [],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        metadata = {
            "index_version": SESSION_RECORD_INDEX_VERSION,
            "event_id": int(event_id),
            "message_id": message_id,
            "source": "conversation_events",
            "event_kind": str(event_kind or ""),
            "role": str(role or ""),
        }
        self.db.upsert_session_record(
            session_id=session_id,
            record_id="event:%s" % int(event_id),
            record_type=str(event_kind or "event"),
            role=str(role or ""),
            title="[%s/%s] %s" % (str(event_kind or "event"), str(role or ""), shorten(str(content or ""), 96)),
            content=searchable,
            search_text=searchable,
            metadata_json=json.dumps(metadata, ensure_ascii=False),
            created_at=created_at,
        )

    def _render_tool_event_content(self, payload: Dict[str, object]) -> str:
        """Render a tool event as compact text for conversation-event windows."""
        parts = [
            "Tool: %s" % payload.get("tool", ""),
            "Arguments: %s" % json.dumps(payload.get("arguments", {}), ensure_ascii=False),
            "Output: %s" % json.dumps(payload.get("output", {}), ensure_ascii=False),
        ]
        if payload.get("error"):
            parts.append("Error: %s" % payload.get("error"))
        return "\n".join(parts)

    def _render_tool_event_search_text(self, payload: Dict[str, object]) -> str:
        """Render the original tool-event payload fields used for indexed retrieval."""
        return json.dumps(
            {
                "event_id": payload.get("event_id") or payload.get("id") or "",
                "tool": payload.get("tool", ""),
                "call_id": payload.get("call_id", ""),
                "arguments": payload.get("arguments", {}),
                "output": payload.get("output", {}),
                "error": payload.get("error"),
                "tool_round": payload.get("tool_round", ""),
                "created_at": payload.get("created_at", ""),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def _index_tool_event_payload(
        self,
        session_id: str,
        payload: Dict[str, object],
        manifest: Optional[Dict[str, object]] = None,
    ) -> None:
        """Index one non-retrieval tool event using its original payload text."""
        tool_name = str(payload.get("tool") or "").strip()
        if not tool_name or tool_name in RETRIEVAL_TOOL_EVENT_NAMES:
            return
        manifest = dict(manifest or {})
        event_id = str(payload.get("event_id") or manifest.get("event_id") or payload.get("id") or payload.get("call_id") or "").strip()
        if not event_id:
            return
        archive_path = str(payload.get("archive_path") or manifest.get("archive_path") or "")
        indexed_payload = dict(payload)
        indexed_payload.setdefault("event_id", event_id)
        indexed_payload.setdefault("archive_path", archive_path)
        if not indexed_payload.get("created_at"):
            indexed_payload["created_at"] = manifest.get("created_at") or ""
        self.db.upsert_tool_event_index(
            session_id=session_id,
            event_id=event_id,
            tool=tool_name,
            call_id=str(payload.get("call_id") or manifest.get("call_id") or ""),
            created_at=str(payload.get("created_at") or manifest.get("created_at") or ""),
            archive_path=archive_path,
            status=str(manifest.get("status") or ("error" if payload.get("error") else "ok")),
            search_text=self._render_tool_event_search_text(indexed_payload),
            metadata_json=json.dumps(
                {
                    "archive_path": archive_path,
                    "archive_format": manifest.get("archive_format") or payload.get("archive_format") or "",
                    "payload_sha256": manifest.get("payload_sha256") or payload.get("payload_sha256") or "",
                    "index_version": TOOL_EVENT_INDEX_VERSION,
                },
                ensure_ascii=False,
            ),
        )

    def _tool_event_archive_path(self, session_id: str, event_id: str):
        """Return the gzip archive path for one complete tool event."""
        safe_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(event_id or "").strip())
        if not safe_id:
            safe_id = "tool-%s" % uuid.uuid4().hex[:12]
        return self.paths.session_tool_event_archives_dir(session_id) / ("%s.json.gz" % safe_id)

    def _archive_tool_event_payload(self, session_id: str, payload: Dict[str, object]) -> Dict[str, object]:
        """Write a complete tool event archive and return a lightweight manifest."""
        archive_dir = self.paths.session_tool_event_archives_dir(session_id)
        ensure_directory(archive_dir)
        event_id = str(payload.get("event_id") or payload.get("id") or payload.get("call_id") or "tool-%s" % uuid.uuid4().hex[:12])
        event_payload = dict(payload)
        event_payload.setdefault("event_id", event_id)
        event_payload.setdefault("created_at", utc_now())
        raw_text = json.dumps(event_payload, ensure_ascii=False, sort_keys=True)
        output_text = json.dumps(event_payload.get("output", {}), ensure_ascii=False, sort_keys=True)
        tool_name = str(event_payload.get("tool") or "").strip()
        if tool_name in RETRIEVAL_TOOL_EVENT_NAMES:
            output_preview = "(retrieval tool result omitted from searchable records; full payload archived)"
        else:
            output_preview = shorten(output_text, 900)
        archive_path = self._tool_event_archive_path(session_id, event_id)
        with gzip.open(archive_path, "wt", encoding="utf-8") as handle:
            handle.write(raw_text)
            handle.write("\n")
        return {
            "event_id": event_id,
            "created_at": event_payload.get("created_at", ""),
            "tool": event_payload.get("tool", ""),
            "call_id": event_payload.get("call_id", ""),
            "tool_round": event_payload.get("tool_round", ""),
            "status": "error" if event_payload.get("error") else "ok",
            "error": event_payload.get("error"),
            "arguments_preview": shorten(json.dumps(event_payload.get("arguments", {}), ensure_ascii=False), 500),
            "output_preview": output_preview,
            "archive_path": archive_path.relative_to(self.paths.home).as_posix(),
            "archive_format": "json.gz",
            "payload_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            "has_full_payload": True,
        }

    def _upsert_tool_session_record(
        self,
        *,
        session_id: str,
        payload: Dict[str, object],
        manifest: Dict[str, object],
    ) -> None:
        """Index one non-retrieval tool interaction as a unified session record."""
        tool_name = str(payload.get("tool") or manifest.get("tool") or "").strip()
        if not tool_name or tool_name in RETRIEVAL_TOOL_EVENT_NAMES:
            return
        event_id = str(payload.get("event_id") or manifest.get("event_id") or payload.get("call_id") or "").strip()
        if not event_id:
            return
        archive_path = str(manifest.get("archive_path") or payload.get("archive_path") or "")
        created_at = str(payload.get("created_at") or manifest.get("created_at") or "")
        content = self._render_tool_event_search_text(
            {
                **dict(payload or {}),
                "event_id": event_id,
                "archive_path": archive_path,
                "created_at": created_at,
            }
        )
        metadata = {
            "index_version": SESSION_RECORD_INDEX_VERSION,
            "source": "tool_events",
            "event_id": event_id,
            "tool": tool_name,
            "call_id": str(payload.get("call_id") or manifest.get("call_id") or ""),
            "archive_path": archive_path,
            "archive_format": str(manifest.get("archive_format") or payload.get("archive_format") or ""),
            "payload_sha256": str(manifest.get("payload_sha256") or payload.get("payload_sha256") or ""),
            "is_retrieval_tool": False,
        }
        self.db.upsert_session_record(
            session_id=session_id,
            record_id="tool:%s" % event_id,
            record_type="tool_interaction",
            role="tool",
            title="[tool/%s] %s" % (tool_name, shorten(content, 96)),
            content=content,
            search_text=content,
            metadata_json=json.dumps(metadata, ensure_ascii=False),
            created_at=created_at,
        )

    def _read_tool_event_archive(self, archive_path: str, expected_hash: str = "") -> Dict[str, object]:
        """Read a complete tool event archive, returning an empty dict on failure."""
        path = self.paths.home / str(archive_path or "")
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                text = handle.read()
            if expected_hash:
                actual = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()
                if actual != expected_hash:
                    return {}
            payload = json.loads(text)
            return dict(payload or {}) if isinstance(payload, dict) else {}
        except (OSError, ValueError):
            return {}

    def append_tool_event(self, session_id: str, payload: Dict[str, object]) -> None:
        """Record a tool event for traceability."""
        manifest = self._archive_tool_event_payload(session_id, payload)
        if str(payload.get("tool") or "").strip() not in RETRIEVAL_TOOL_EVENT_NAMES:
            payload_for_index = dict(payload)
            payload_for_index.setdefault("event_id", manifest.get("event_id"))
            payload_for_index.setdefault("archive_path", manifest.get("archive_path"))
            self._upsert_tool_session_record(
                session_id=session_id,
                payload=payload_for_index,
                manifest=manifest,
            )
        append_jsonl(self.paths.session_tool_events_file(session_id), manifest)

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
        rows = []
        for item in read_jsonl(self.paths.session_tool_events_file(session_id)):
            if not isinstance(item, dict):
                continue
            archive_path = str(item.get("archive_path") or "")
            if archive_path:
                archived = self._read_tool_event_archive(archive_path, str(item.get("payload_sha256") or ""))
                if archived:
                    archived.setdefault("archive_path", archive_path)
                    archived.setdefault("event_id", item.get("event_id") or archived.get("call_id") or "")
                    rows.append(archived)
                    continue
            rows.append(dict(item))
        return rows

    def index_session_records_for_session(self, session_id: str) -> None:
        """Ensure legacy session files are represented in the unified session-record index."""
        indexed = self.db.fetch_session_record_index_records(session_id)
        for message in self.get_all_messages(session_id):
            record_id = "msg:%s" % int(message["id"])
            metadata = dict(indexed.get(record_id) or {})
            if int(metadata.get("index_version") or 0) >= SESSION_RECORD_INDEX_VERSION:
                continue
            self._upsert_message_session_record(
                session_id=session_id,
                message_id=int(message["id"]),
                role=str(message["role"]),
                content=str(message["content"]),
                metadata=dict(message.get("metadata") or {}),
                created_at=str(message.get("created_at") or ""),
            )

        indexed = self.db.fetch_session_record_index_records(session_id)
        for event in self.get_conversation_events(session_id):
            event_kind = str(event.get("event_kind") or "")
            if event_kind in {"message", "tool_result"}:
                continue
            record_id = "event:%s" % int(event["id"])
            metadata = dict(indexed.get(record_id) or {})
            if int(metadata.get("index_version") or 0) >= SESSION_RECORD_INDEX_VERSION:
                continue
            self._upsert_event_session_record(
                session_id=session_id,
                event_id=int(event["id"]),
                event_kind=event_kind,
                role=str(event.get("role") or ""),
                content=str(event.get("content") or ""),
                payload=dict(event.get("payload") or {}),
                message_id=event.get("message_id"),
                created_at=str(event.get("created_at") or ""),
            )

        indexed = self.db.fetch_session_record_index_records(session_id)
        for manifest in read_jsonl(self.paths.session_tool_events_file(session_id)):
            if not isinstance(manifest, dict):
                continue
            tool_name = str(manifest.get("tool") or "").strip()
            if not tool_name or tool_name in RETRIEVAL_TOOL_EVENT_NAMES:
                continue
            event_id = str(manifest.get("event_id") or manifest.get("id") or manifest.get("call_id") or "").strip()
            if not event_id:
                continue
            record_id = "tool:%s" % event_id
            metadata = dict(indexed.get(record_id) or {})
            if int(metadata.get("index_version") or 0) >= SESSION_RECORD_INDEX_VERSION:
                continue
            archive_path = str(manifest.get("archive_path") or "")
            event = self._read_tool_event_archive(archive_path, str(manifest.get("payload_sha256") or "")) if archive_path else {}
            if not event:
                event = dict(manifest)
            event.setdefault("event_id", event_id)
            event.setdefault("archive_path", archive_path)
            self._upsert_tool_session_record(session_id=session_id, payload=event, manifest=dict(manifest))

    def _index_session_records_for_scope(self, *, project_slug: Optional[str] = None, session_id: Optional[str] = None) -> None:
        """Backfill unified session records for a query scope."""
        if session_id:
            self.index_session_records_for_session(session_id)
            return
        for row in self.db.fetch_session_ids(project_slug=project_slug):
            self.index_session_records_for_session(str(row["id"]))

    def _parse_session_record_row(self, row) -> Dict[str, object]:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except ValueError:
            metadata = {}
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "record_id": row["record_id"],
            "record_type": row["record_type"],
            "role": row["role"],
            "title": row["title"],
            "content": row["content"],
            "search_text": row["search_text"],
            "metadata": dict(metadata or {}),
            "created_at": row["created_at"],
            "project_slug": row["project_slug"] if "project_slug" in row.keys() else "",
        }

    def search_session_records(
        self,
        query: str,
        limit: int = 5,
        *,
        project_slug: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, object]]:
        """Search the unified session-record index."""
        self._index_session_records_for_scope(project_slug=project_slug, session_id=session_id)
        results = []
        for row in self.db.search_session_records(
            query,
            limit,
            project_slug=project_slug,
            session_id=session_id,
        ):
            item = self._parse_session_record_row(row)
            item["score"] = overlap_score(query, str(item.get("search_text") or item.get("content") or ""))
            results.append(item)
        results.sort(
            key=lambda item: (float(item.get("score") or 0.0), str(item.get("created_at") or ""), int(item.get("id") or 0)),
            reverse=True,
        )
        return results[: max(1, int(limit or 5))]

    def get_session_record_window(self, session_id: str, record_id: str, before: int = 4, after: int = 6) -> List[Dict[str, object]]:
        """Return a local window around one unified session record."""
        self.index_session_records_for_session(session_id)
        rows = [self._parse_session_record_row(row) for row in self.db.fetch_all_session_records(session_id)]
        if not rows:
            return []
        anchor_index = len(rows) - 1
        for index, item in enumerate(rows):
            if str(item.get("record_id") or "") == str(record_id or ""):
                anchor_index = index
                break
        start = max(0, anchor_index - max(0, int(before)))
        end = min(len(rows), anchor_index + max(0, int(after)) + 1)
        return rows[start:end]

    def index_tool_events_for_session(self, session_id: str) -> None:
        """Ensure archived non-retrieval tool events are available in the FTS index."""
        indexed_records = self.db.fetch_tool_event_index_records(session_id)
        for manifest in read_jsonl(self.paths.session_tool_events_file(session_id)):
            if not isinstance(manifest, dict):
                continue
            event_id = str(manifest.get("event_id") or manifest.get("id") or manifest.get("call_id") or "").strip()
            if not event_id:
                continue
            indexed = dict(indexed_records.get(event_id) or {})
            if int(indexed.get("index_version") or 0) >= TOOL_EVENT_INDEX_VERSION:
                continue
            tool_name = str(manifest.get("tool") or "").strip()
            if not tool_name or tool_name in RETRIEVAL_TOOL_EVENT_NAMES:
                continue
            archive_path = str(manifest.get("archive_path") or "")
            event = self._read_tool_event_archive(archive_path, str(manifest.get("payload_sha256") or "")) if archive_path else {}
            if not event:
                event = dict(manifest)
            event.setdefault("event_id", event_id)
            event.setdefault("archive_path", archive_path)
            self._index_tool_event_payload(session_id, event, dict(manifest))

    def search_tool_events(self, session_id: str, query: str, limit: int = 8) -> List[Dict[str, object]]:
        """Search non-retrieval tool interactions through the unified session-record index."""
        self.index_session_records_for_session(session_id)
        rows = self.search_session_records(
            query,
            limit=max(1, int(limit or 8)) * 4,
            session_id=session_id,
        )
        results: List[Dict[str, object]] = []
        for row in rows:
            if str(row.get("record_type") or "") != "tool_interaction":
                continue
            metadata = dict(row.get("metadata") or {})
            tool_name = str(metadata.get("tool") or "").strip()
            if not tool_name or tool_name in RETRIEVAL_TOOL_EVENT_NAMES:
                continue
            archive_path = str(metadata.get("archive_path") or "")
            payload: Dict[str, object] = {}
            if archive_path:
                payload = self._read_tool_event_archive(archive_path, str(metadata.get("payload_sha256") or ""))
            if not payload:
                try:
                    parsed = json.loads(str(row.get("content") or "{}"))
                    payload = dict(parsed or {}) if isinstance(parsed, dict) else {}
                except ValueError:
                    payload = {}
            if str(payload.get("tool") or tool_name or "").strip() in RETRIEVAL_TOOL_EVENT_NAMES:
                continue
            payload.setdefault("event_id", metadata.get("event_id") or "")
            payload.setdefault("session_id", row.get("session_id") or session_id)
            payload.setdefault("tool", tool_name)
            payload.setdefault("call_id", metadata.get("call_id") or "")
            payload.setdefault("created_at", row.get("created_at") or "")
            payload.setdefault("archive_path", archive_path)
            payload["_search_text"] = str(row.get("search_text") or row.get("content") or "")
            payload["_search_score"] = float(row.get("score") or overlap_score(query, payload["_search_text"]))
            results.append(payload)
        results.sort(
            key=lambda item: (float(item.get("_search_score") or 0.0), str(item.get("created_at") or "")),
            reverse=True,
        )
        return results[: max(1, int(limit or 8))]

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

        results = []
        for row in self.db.search_conversation_events(query, limit * 4, project_slug=project_slug):
            payload = _payload(row["payload_json"])
            if str(row["event_kind"] or "") == "tool_result":
                continue
            results.append(
                {
                "id": row["id"],
                "session_id": row["session_id"],
                "message_id": row["message_id"],
                "event_kind": row["event_kind"],
                "role": row["role"],
                "content": row["content"],
                    "payload": payload,
                "created_at": row["created_at"],
                "project_slug": row["project_slug"],
            }
            )
            if len(results) >= limit:
                break
        return results

    def mark_closed(self, session_id: str) -> None:
        """Mark a session as closed."""
        updated_at = utc_now()
        session_meta = read_json(self.paths.session_meta_file(session_id), default={}) or {}
        session_meta["updated_at"] = updated_at
        session_meta["status"] = "closed"
        write_json(self.paths.session_meta_file(session_id), session_meta)
        self.db.update_session(session_id, updated_at=updated_at, status="closed")
