"""Crash-safe execution journal for provider-requested tool calls."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Dict, List, Optional, Sequence

from moonshine.utils import read_jsonl, shorten, utc_now


TOOL_EXECUTION_STARTED = "tool_execution_started"
TOOL_EXECUTION_FINISHED = "tool_execution_finished"
TOOL_EXECUTION_AMBIGUOUS = "tool_execution_ambiguous"
TOOL_EXECUTION_BLOCKED = "tool_execution_blocked"


class ToolExecutionJournal(object):
    """Persist tool dispatch boundaries and fail closed after interrupted turns.

    The journal does not claim exactly-once execution. Instead it establishes a
    conservative contract: write intent before dispatch, write a terminal marker
    before the next call begins, and never automatically dispatch more tools in a
    session when an earlier tool execution or tool-bearing turn has ambiguous
    completion.
    """

    def __init__(self, runtime: Dict[str, object]):
        self.runtime = runtime
        self.store = runtime.get("session_store") if isinstance(runtime, dict) else None
        self.session_id = str(runtime.get("session_id") or "").strip() if isinstance(runtime, dict) else ""
        self.paths = getattr(self.store, "paths", None) if self.store is not None else None
        self.turn_sequence, self.open_turn_sequences = self._turn_lifecycle()

    @property
    def enabled(self) -> bool:
        """Return whether durable session journaling is available."""
        return bool(self.store is not None and self.session_id and hasattr(self.store, "append_conversation_event"))

    def _render_json(self, value: object) -> str:
        """Render a deterministic JSON-ish representation for hashes and previews."""
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    def _fingerprint(self, value: object, preview_chars: int = 800) -> Dict[str, str]:
        """Return bounded trace metadata without duplicating large tool payloads."""
        rendered = self._render_json(value)
        return {
            "sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            "preview": shorten(rendered, preview_chars),
        }

    def _turn_lifecycle(self):
        """Return monotonically numbered turn starts and currently open sequences."""
        if self.paths is None or not self.session_id:
            return 0, []
        turn_events = [
            item
            for item in read_jsonl(self.paths.session_turn_events_file(self.session_id))
            if isinstance(item, dict) and str(item.get("type") or "") in {"turn_started", "turn_completed"}
        ]
        sequence = 0
        open_sequences: List[int] = []
        for item in turn_events:
            if str(item.get("type") or "") == "turn_started":
                sequence += 1
                open_sequences.append(sequence)
            elif open_sequences:
                # A later resumed turn can complete while an older interrupted
                # turn remains unresolved, so pair completion with the newest start.
                open_sequences.pop()
        return sequence, open_sequences

    def _payload(self, event: Dict[str, object]) -> Dict[str, object]:
        payload = event.get("payload") or {}
        return dict(payload) if isinstance(payload, dict) else {}

    def _events(self) -> List[Dict[str, object]]:
        if self.store is None or not self.session_id or not hasattr(self.store, "get_conversation_events"):
            return []
        return list(self.store.get_conversation_events(self.session_id))

    def blockers(self) -> List[Dict[str, object]]:
        """Return unresolved executions plus any prior interrupted tool-bearing turn."""
        events = self._events()
        active: Dict[str, Dict[str, object]] = {}
        for event in events:
            kind = str(event.get("event_kind") or "")
            if kind not in {TOOL_EXECUTION_STARTED, TOOL_EXECUTION_FINISHED, TOOL_EXECUTION_AMBIGUOUS}:
                continue
            payload = self._payload(event)
            execution_id = str(payload.get("execution_id") or "").strip()
            if not execution_id:
                continue
            if kind == TOOL_EXECUTION_FINISHED:
                active.pop(execution_id, None)
                continue
            record = dict(payload)
            record["state"] = "ambiguous" if kind == TOOL_EXECUTION_AMBIGUOUS else "started"
            record["event_id"] = event.get("id")
            active[execution_id] = record

        blockers = list(active.values())
        prior_turn = self._prior_interrupted_tool_turn(events)
        if prior_turn is not None:
            blockers.append(prior_turn)
        return blockers

    def _prior_interrupted_tool_turn(self, events: Sequence[Dict[str, object]]) -> Optional[Dict[str, object]]:
        """Return a prior open turn that executed tools before a later turn began."""
        # During a normal dispatch the current turn itself is open. Older open
        # sequences represent turns that survived into a later user turn.
        if len(self.open_turn_sequences) <= 1:
            return None
        prior_sequences = set(self.open_turn_sequences[:-1])
        for event in events:
            if str(event.get("event_kind") or "") != TOOL_EXECUTION_STARTED:
                continue
            payload = self._payload(event)
            try:
                turn_sequence = int(payload.get("turn_sequence") or 0)
            except (TypeError, ValueError):
                turn_sequence = 0
            if turn_sequence not in prior_sequences:
                continue
            return {
                "state": "interrupted_turn",
                "execution_id": str(payload.get("execution_id") or "turn:%s" % turn_sequence),
                "tool": str(payload.get("tool") or "unknown"),
                "call_id": str(payload.get("call_id") or ""),
                "turn_sequence": turn_sequence,
            }
        return None

    def _append(self, event_kind: str, content: str, payload: Dict[str, object]) -> None:
        if not self.enabled:
            return
        self.store.append_conversation_event(
            self.session_id,
            event_kind=event_kind,
            role="tool",
            content=content,
            payload=dict(payload),
        )

    def _mark_session_interrupted(self, interruption: Dict[str, object]) -> None:
        if self.store is None or not self.session_id:
            return
        now = utc_now()
        if hasattr(self.store, "update_session_meta"):
            self.store.update_session_meta(
                self.session_id,
                status="interrupted",
                updated_at=now,
                interrupted_tool_execution=dict(interruption),
            )
        db = getattr(self.store, "db", None)
        if db is not None and hasattr(db, "update_session"):
            db.update_session(self.session_id, updated_at=now, status="interrupted")

    def begin(self, call: object) -> str:
        """Write a durable intent immediately before dispatch."""
        execution_id = "tool-exec-%s" % uuid.uuid4().hex[:12]
        tool_name = str(getattr(call, "name", "") or "")
        call_id = str(getattr(call, "call_id", "") or "")
        arguments = dict(getattr(call, "arguments", {}) or {})
        arguments_fingerprint = self._fingerprint(arguments)
        payload = {
            "execution_id": execution_id,
            "tool": tool_name,
            "call_id": call_id,
            "arguments_sha256": arguments_fingerprint["sha256"],
            "arguments_preview": arguments_fingerprint["preview"],
            "turn_sequence": self.turn_sequence,
            "tool_round": self.runtime.get("_current_tool_round", ""),
            "started_at": utc_now(),
        }
        self._append(
            TOOL_EXECUTION_STARTED,
            "Tool execution started: %s (%s)" % (tool_name, call_id or execution_id),
            payload,
        )
        return execution_id

    def finish(self, call: object, execution_id: str, *, output: object, error: Optional[str]) -> None:
        """Write a terminal marker before the next call is dispatched."""
        output_fingerprint = self._fingerprint(output, preview_chars=1200)
        payload = {
            "execution_id": execution_id,
            "tool": str(getattr(call, "name", "") or ""),
            "call_id": str(getattr(call, "call_id", "") or ""),
            "turn_sequence": self.turn_sequence,
            "tool_round": self.runtime.get("_current_tool_round", ""),
            "outcome": "error" if error else "ok",
            "error": shorten(str(error or ""), 500),
            "output_preview": output_fingerprint["preview"],
            "output_sha256": output_fingerprint["sha256"],
            "finished_at": utc_now(),
        }
        self._append(
            TOOL_EXECUTION_FINISHED,
            "Tool execution finished: %s (%s)" % (payload["tool"], payload["call_id"] or execution_id),
            payload,
        )

    def mark_ambiguous(self, call: object, execution_id: str, exc: BaseException) -> None:
        """Record a process-level interruption whose completion is unknowable."""
        arguments_fingerprint = self._fingerprint(dict(getattr(call, "arguments", {}) or {}))
        payload = {
            "execution_id": execution_id,
            "tool": str(getattr(call, "name", "") or ""),
            "call_id": str(getattr(call, "call_id", "") or ""),
            "arguments_sha256": arguments_fingerprint["sha256"],
            "arguments_preview": arguments_fingerprint["preview"],
            "turn_sequence": self.turn_sequence,
            "tool_round": self.runtime.get("_current_tool_round", ""),
            "state": "ambiguous",
            "interruption_type": type(exc).__name__,
            "interruption": shorten(str(exc), 500),
            "interrupted_at": utc_now(),
        }
        self._append(
            TOOL_EXECUTION_AMBIGUOUS,
            "Tool execution became ambiguous after interruption: %s (%s)"
            % (payload["tool"], payload["call_id"] or execution_id),
            payload,
        )
        self._mark_session_interrupted(
            {
                "execution_id": execution_id,
                "tool": payload["tool"],
                "call_id": payload["call_id"],
                "state": "ambiguous",
            }
        )

    def blocked_results(self, calls: List[object], blockers: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
        """Return provider-visible errors without dispatching any requested tool."""
        first = dict(blockers[0]) if blockers else {}
        blocker_tool = str(first.get("tool") or "unknown")
        blocker_call_id = str(first.get("call_id") or "unknown")
        blocker_execution_id = str(first.get("execution_id") or "unknown")
        blocker_state = str(first.get("state") or "ambiguous")
        self._mark_session_interrupted(
            {
                "execution_id": blocker_execution_id,
                "tool": blocker_tool,
                "call_id": blocker_call_id,
                "state": blocker_state,
            }
        )
        reason = (
            "a prior tool-bearing turn was interrupted before Moonshine durably completed the turn"
            if blocker_state == "interrupted_turn"
            else "a prior tool execution has ambiguous completion"
        )
        message = (
            "Tool dispatch is blocked because %s: tool=%s, call_id=%s, execution_id=%s. "
            "Moonshine will not replay or dispatch additional tools automatically because prior "
            "handlers may already have produced external side effects. Inspect the session records "
            "and continue in a fresh session once the ambiguity is resolved."
            % (reason, blocker_tool, blocker_call_id, blocker_execution_id)
        )
        public_blockers = [
            {
                key: item.get(key)
                for key in ("state", "execution_id", "tool", "call_id", "turn_sequence", "started_at", "interrupted_at")
                if item.get(key) not in {None, ""}
            }
            for item in blockers
        ]
        results: List[Dict[str, object]] = []
        for call in calls:
            result = {
                "name": getattr(call, "name", ""),
                "call_id": getattr(call, "call_id", ""),
                "arguments": getattr(call, "arguments", {}),
                "output": {
                    "status": "blocked_interrupted_execution",
                    "message": message,
                    "ambiguous_executions": public_blockers,
                },
                "error": message,
            }
            results.append(result)
            self.runtime.setdefault("_tool_results_in_round", []).append(result)
            self._append(
                TOOL_EXECUTION_BLOCKED,
                "Blocked tool dispatch: %s" % str(getattr(call, "name", "") or ""),
                {
                    "tool": str(getattr(call, "name", "") or ""),
                    "call_id": str(getattr(call, "call_id", "") or ""),
                    "blocked_at": utc_now(),
                    "blocker_states": [str(item.get("state") or "") for item in blockers],
                    "ambiguous_execution_ids": [str(item.get("execution_id") or "") for item in blockers],
                },
            )
        return results
