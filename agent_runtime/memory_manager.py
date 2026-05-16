"""High-level memory orchestration for Moonshine."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from moonshine.agent_runtime.auto_extract import BackgroundMemoryExtractionExecutor
from moonshine.agent_runtime.extraction import (
    ExtractedItems,
    HeuristicMemoryExtractor,
    LLMSkillMemoryExtractor,
    has_strong_memory_signal,
)
from moonshine.agent_runtime.memory_schemas import ALLOWED_DYNAMIC_ALIASES, STATUS_ENUM
from moonshine.agent_runtime.memory_provider import ContextBundle, MemoryProvider
from moonshine.agent_runtime.research_index import ResearchIndexStore
from moonshine.agent_runtime.review import ReviewReport, build_review_report
from moonshine.moonshine_cli.config import AppConfig
from moonshine.moonshine_constants import (
    MoonshinePaths,
    RESEARCH_MEMORY_CHANNELS,
    normalize_research_channel_name,
)
from moonshine.storage.dynamic_memory_store import DynamicMemoryEntry, DynamicMemoryStore
from moonshine.storage.knowledge_store import KnowledgeStore
from moonshine.storage.session_store import SessionStore
from moonshine.storage.static_context_store import StaticContextStore
from moonshine.utils import append_jsonl, deterministic_slug, overlap_score, parse_utc_timestamp, read_jsonl, read_text, shorten, utc_now


RESEARCH_ARTIFACT_CHANNELS = {
    "candidate_problem": "events",
    "problem_review": "big_decisions",
    "active_problem": "big_decisions",
    "stage_transition": "events",
    "example": "toy_examples",
    "counterexample": "counterexamples",
    "special_case_check": "special_case_checks",
    "novelty_note": "novelty_notes",
    "subgoal_plan": "subgoals",
    "solve_attempt": "solve_steps",
    "lemma_candidate": "immediate_conclusions",
    "conclusion": "immediate_conclusions",
    "verification_report": "verification_reports",
    "failed_path": "failed_paths",
    "branch_update": "branch_states",
    "decision": "big_decisions",
    "checkpoint": "events",
    "note": "events",
    "artifact": "events",
}


class MemoryManager(MemoryProvider):
    """Coordinate the implemented memory layers and lifecycle-driven extraction."""

    def __init__(
        self,
        paths: MoonshinePaths,
        config: AppConfig,
        session_store: SessionStore,
        provider=None,
        skill_manager=None,
    ):
        self.paths = paths
        self.config = config
        self.session_store = session_store
        self.provider = provider
        self.skill_manager = skill_manager
        self.static_store = StaticContextStore(paths)
        self.dynamic_store = DynamicMemoryStore(paths)
        self.knowledge_store = KnowledgeStore(paths, config.memory)
        self.research_index = ResearchIndexStore(paths)
        self.extractor = HeuristicMemoryExtractor()
        self.llm_extractor = LLMSkillMemoryExtractor(provider=provider, skill_manager=skill_manager) if skill_manager is not None else None
        self.auto_extract_executor = BackgroundMemoryExtractionExecutor(
            self,
            max_workers=int(getattr(config.memory, "auto_extract_max_workers", 1)),
        )

    def set_provider(self, provider) -> None:
        """Update the provider used for LLM-driven extraction."""
        self.provider = provider
        if self.llm_extractor is not None:
            self.llm_extractor.provider = provider

    def ensure_project(self, project_slug: str) -> None:
        """Track that a project is active."""
        summary = "Project '%s' was active at %s." % (project_slug, utc_now())
        entry = self.dynamic_store.make_entry(
            alias="project-active",
            slug=deterministic_slug("active-project", project_slug, prefix="project"),
            title="Active Project: %s" % project_slug,
            summary=summary,
            body=summary,
            source="system",
            project_slug=project_slug,
            tags=["project", "active"],
            source_excerpt=summary,
        )
        self.dynamic_store.write_entry(entry)
        self.dynamic_store.rebuild_index()

    def remember_explicit(
        self,
        text: str,
        project_slug: Optional[str] = None,
        session_id: str = "",
        source_message_role: str = "user",
    ) -> DynamicMemoryEntry:
        """Persist an explicit memory instruction."""
        entry = self.dynamic_store.make_entry(
            alias="feedback-explicit",
            slug=deterministic_slug("explicit-memory", text, prefix="explicit"),
            title="Explicit Memory Request",
            summary=shorten(text, 96),
            body=text.strip(),
            source="explicit",
            project_slug=project_slug,
            tags=["explicit", "memory"],
            source_session_id=session_id,
            source_message_role=source_message_role,
            source_excerpt=shorten(text, 160),
        )
        self.dynamic_store.write_entry(entry)
        self.dynamic_store.rebuild_index()
        return entry

    def write_manual_entry(
        self,
        *,
        alias: str,
        title: str,
        body: str,
        project_slug: Optional[str] = None,
        summary: Optional[str] = None,
        source: str = "manual",
        session_id: str = "",
        source_message_role: str = "",
        source_excerpt: str = "",
    ) -> DynamicMemoryEntry:
        """Write a manual dynamic memory entry."""
        entry = self.dynamic_store.make_entry(
            alias=alias,
            slug=deterministic_slug(title, summary or body, prefix=alias),
            title=title,
            summary=summary or shorten(body, 96),
            body=body,
            source=source,
            project_slug=project_slug,
            tags=[alias],
            source_session_id=session_id,
            source_message_role=source_message_role,
            source_excerpt=source_excerpt or shorten(body, 160),
        )
        self.dynamic_store.write_entry(entry)
        self.dynamic_store.rebuild_index()
        return entry

    def _session_elapsed_minutes(self, session_id: str) -> float:
        """Return minutes since the last extraction-related checkpoint."""
        if not session_id:
            return 0.0
        meta = self.session_store.get_session_meta(session_id)
        anchor = str(meta.get("last_auto_extract_at") or meta.get("started_at") or "")
        anchor_dt = parse_utc_timestamp(anchor)
        now_dt = parse_utc_timestamp(utc_now())
        if anchor_dt is None or now_dt is None:
            return 0.0
        return max(0.0, (now_dt - anchor_dt).total_seconds() / 60.0)

    def _pending_message_count(self, session_id: str) -> int:
        """Return the current pending message count for extraction cadence."""
        if not session_id:
            return 0
        return int(self.session_store.get_session_meta(session_id).get("pending_message_count", 0) or 0)

    def _update_pending_state(
        self,
        session_id: str,
        *,
        pending_message_count: Optional[int] = None,
        touched: bool = False,
        in_progress: Optional[bool] = None,
        task_id: Optional[str] = None,
    ) -> None:
        """Update extraction-related session metadata."""
        if not session_id:
            return
        changes = {}
        if pending_message_count is not None:
            changes["pending_message_count"] = int(max(0, pending_message_count))
        if touched:
            changes["last_auto_extract_at"] = utc_now()
        if in_progress is not None:
            changes["auto_extract_in_progress"] = bool(in_progress)
        if task_id is not None:
            changes["last_auto_extract_task_id"] = str(task_id)
        if changes:
            self.session_store.update_session_meta(session_id, **changes)

    def _should_run_post_turn_trigger(
        self,
        user_message: str,
        assistant_message: str,
        session_id: str,
        *,
        pending_tool_calls: bool = False,
    ) -> tuple[bool, int, str]:
        """Decide whether the post-turn trigger should invoke the evaluator."""
        if not self.config.memory.auto_extract:
            return False, self._pending_message_count(session_id), "disabled"

        pending = self._pending_message_count(session_id) + 2
        if pending_tool_calls:
            return False, pending, "pending_tool_calls"

        strong_signal = has_strong_memory_signal(user_message, assistant_message)
        enough_messages = pending >= int(self.config.memory.auto_extract_min_messages)
        enough_minutes = self._session_elapsed_minutes(session_id) >= float(self.config.memory.auto_extract_min_minutes)
        cadence_ready = enough_messages and enough_minutes
        should_run = strong_signal or cadence_ready
        if strong_signal:
            reason = "strong_signal"
        elif cadence_ready:
            reason = "cadence"
        else:
            reason = "waiting_for_cadence"
        return should_run, pending, reason

    def _build_payload(
        self,
        *,
        trigger: str,
        project_slug: Optional[str],
        session_id: str,
        source_text: str,
        user_message: str = "",
        assistant_message: str = "",
    ) -> Dict[str, object]:
        """Build the lifecycle payload sent to the evaluator and extraction skills."""
        return {
            "trigger": trigger,
            "project_slug": project_slug or "",
            "session_id": session_id,
            "user_message": user_message,
            "assistant_message": assistant_message,
            "source_text": source_text,
        }

    def _append_extraction_audit(
        self,
        *,
        event: str,
        trigger: str,
        session_id: str = "",
        project_slug: Optional[str] = None,
        result: Optional[Dict[str, object]] = None,
        reason: str = "",
        error: str = "",
        source_excerpt: str = "",
        task_id: str = "",
    ) -> None:
        """Append a non-retrieval audit record for memory extraction activity."""
        result = dict(result or {})
        append_jsonl(
            self.paths.memory_audit_log,
            {
                "event": event,
                "trigger": trigger,
                "session_id": session_id,
                "project_slug": project_slug or "",
                "task_id": task_id,
                "entries": int(result.get("entries", 0) or 0),
                "conclusions": int(result.get("conclusions", 0) or 0),
                "updated_files": list(result.get("updated_files") or []),
                "reason": reason,
                "error": shorten(error, 1200),
                "source_excerpt": shorten(source_excerpt, 240),
                "created_at": utc_now(),
            },
        )

    def _apply_extracted_items(
        self,
        extracted: ExtractedItems,
        *,
        project_slug: Optional[str],
        session_id: str,
        source_message_role: str,
        source_excerpt: str,
    ) -> Dict[str, object]:
        """Write validated extracted proposals into the dynamic and knowledge stores."""
        entry_count = 0
        conclusion_count = 0
        updated_files: List[str] = []

        for item in extracted.entries:
            if not self._is_valid_extracted_entry(item):
                continue
            entry = self.dynamic_store.make_entry(
                alias=str(item["alias"]),
                slug=str(item["slug"]),
                title=str(item["title"]),
                summary=str(item["summary"]),
                body=str(item["body"]),
                source=str(item["source"]),
                project_slug=item.get("project_slug"),
                tags=list(item.get("tags") or []),
                source_session_id=session_id,
                source_message_role=source_message_role,
                source_excerpt=shorten(source_excerpt, 160),
            )
            self.dynamic_store.write_entry(entry)
            updated_files.append(str(self.paths.home / entry.relative_path))
            entry_count += 1

        for conclusion in extracted.conclusions:
            if not self._is_valid_extracted_conclusion(conclusion):
                continue
            status = str(conclusion.get("status", "partial") or "partial").strip().lower()
            conclusion_project = str(conclusion.get("project_slug") or project_slug or "general")
            if status != "verified":
                title = str(conclusion["title"])
                statement = str(conclusion["statement"])
                proof_sketch = str(conclusion.get("proof_sketch", ""))
                entry = self.dynamic_store.make_entry(
                    alias="project-lemmas",
                    slug=deterministic_slug("candidate-conclusion", title + "\n" + statement, prefix="candidate-conclusion"),
                    title="Candidate: %s" % title,
                    summary=shorten(statement, 120),
                    body=(
                        "Unverified auto-extracted conclusion candidate.\n\n"
                        "Statement: {statement}\n\n"
                        "Proof sketch/evidence: {proof_sketch}\n\n"
                        "Status: {status}\n"
                        "Source: auto-extract\n"
                    ).format(
                        statement=statement,
                        proof_sketch=proof_sketch or "(none)",
                        status=status or "partial",
                    ),
                    source="auto-extract-candidate",
                    project_slug=conclusion_project,
                    tags=list(conclusion.get("tags") or []) + ["candidate", "unverified"],
                    source_session_id=session_id,
                    source_message_role=source_message_role,
                    source_excerpt=shorten(source_excerpt, 160),
                )
                self.dynamic_store.write_entry(entry)
                updated_files.append(str(self.paths.home / entry.relative_path))
                entry_count += 1
                continue
            conclusion_id = self.knowledge_store.add_conclusion(
                title=str(conclusion["title"]),
                statement=str(conclusion["statement"]),
                proof_sketch=str(conclusion.get("proof_sketch", "")),
                status=status,
                project_slug=conclusion_project,
                tags=list(conclusion.get("tags") or []),
                source_type=str(conclusion.get("source_type", "llm-extract")),
                source_ref=str(conclusion.get("source_ref", session_id or "conversation")),
            )
            updated_files.append(str(self.knowledge_store.entry_path(conclusion_id)))
            conclusion_count += 1

        if entry_count:
            self.dynamic_store.rebuild_index()
        return {"entries": entry_count, "conclusions": conclusion_count, "updated_files": sorted(set(updated_files))}

    def _is_valid_extracted_entry(self, item: Dict[str, object]) -> bool:
        """Return True when a dynamic-memory proposal is safe to write."""
        if not isinstance(item, dict):
            return False
        alias = str(item.get("alias", "") or "").strip()
        if alias not in ALLOWED_DYNAMIC_ALIASES:
            return False
        if alias.startswith("project-") and not str(item.get("project_slug", "") or "").strip():
            return False
        for key in ("slug", "title", "summary", "body", "source"):
            if not str(item.get(key, "") or "").strip():
                return False
        tags = item.get("tags", [])
        return isinstance(tags, list)

    def _is_valid_extracted_conclusion(self, item: Dict[str, object]) -> bool:
        """Return True when a knowledge-memory proposal is safe to write."""
        if not isinstance(item, dict):
            return False
        if not str(item.get("title", "") or "").strip():
            return False
        if not str(item.get("statement", "") or "").strip():
            return False
        status = str(item.get("status", "partial") or "partial").strip().lower()
        if status not in STATUS_ENUM:
            return False
        tags = item.get("tags", [])
        return isinstance(tags, list)

    def _heuristic_from_payload(self, payload: Dict[str, object]) -> ExtractedItems:
        """Fallback extraction when the LLM pipeline is unavailable or fails."""
        source_text = str(payload.get("source_text", "") or "")
        trigger = str(payload.get("trigger", "") or "")
        if trigger == "post_turn":
            return self.extractor.extract(
                str(payload.get("user_message", "") or ""),
                str(payload.get("assistant_message", "") or ""),
                str(payload.get("project_slug", "") or "") or None,
            )
        return self.extractor.extract(
            source_text,
            "",
            str(payload.get("project_slug", "") or "") or None,
        )

    def _run_extraction_pipeline(self, payload: Dict[str, object]) -> ExtractedItems:
        """Run the evaluator plus specialized extraction skills, with heuristic fallback."""
        if self.llm_extractor is None or not self.llm_extractor.can_use_llm():
            return self._heuristic_from_payload(payload)

        decision = self.llm_extractor.evaluate_trigger(payload)
        if not decision.run or not decision.skills:
            return ExtractedItems()

        merged = ExtractedItems()
        for skill_slug in decision.skills:
            extracted = self.llm_extractor.run_extraction_skill(skill_slug, payload)
            merged.entries.extend(extracted.entries)
            merged.conclusions.extend(extracted.conclusions)
        return merged

    def prepare_auto_extract_job(
        self,
        *,
        user_message: str,
        assistant_message: str,
        project_slug: Optional[str],
        session_id: str = "",
        pending_tool_calls: bool = False,
    ) -> Dict[str, object]:
        """Build a post-turn extraction payload when trigger conditions pass."""
        should_run, pending, reason = self._should_run_post_turn_trigger(
            user_message,
            assistant_message,
            session_id,
            pending_tool_calls=pending_tool_calls,
        )
        if not should_run:
            self._update_pending_state(session_id, pending_message_count=pending)
            return {
                "should_run": False,
                "pending_message_count": pending,
                "trigger_reason": reason,
            }

        payload = self._build_payload(
            trigger="post_turn",
            project_slug=project_slug,
            session_id=session_id,
            source_text="User: %s\nAssistant: %s" % (user_message, assistant_message),
            user_message=user_message,
            assistant_message=assistant_message,
        )
        payload["trigger_reason"] = reason
        payload["pending_message_count"] = pending
        self._update_pending_state(
            session_id,
            pending_message_count=pending,
            in_progress=True,
        )
        return {
            "should_run": True,
            "trigger": "post_turn",
            "trigger_reason": reason,
            "pending_message_count": pending,
            "payload": payload,
        }

    def note_auto_extract_deferred(self, *, session_id: str, increment: int = 2) -> None:
        """Record that a turn arrived while a background extraction job was active."""
        pending = self._pending_message_count(session_id) + int(max(0, increment))
        self._update_pending_state(session_id, pending_message_count=pending)

    def run_auto_extract_payload(self, payload: Dict[str, object]) -> Dict[str, object]:
        """Run the actual evaluator plus extraction skills for a prepared payload."""
        extracted = self._run_extraction_pipeline(payload)
        if not extracted.entries and not extracted.conclusions:
            return {"entries": 0, "conclusions": 0, "updated_files": []}
        return self._apply_extracted_items(
            extracted,
            project_slug=str(payload.get("project_slug", "") or "") or None,
            session_id=str(payload.get("session_id", "") or ""),
            source_message_role="user" if payload.get("trigger") == "post_turn" else "system",
            source_excerpt=str(payload.get("user_message") or payload.get("source_text") or ""),
        )

    def complete_auto_extract_job(
        self,
        *,
        session_id: str,
        task_id: str = "",
        success: bool,
        result: Optional[Dict[str, object]] = None,
        error: str = "",
    ) -> None:
        """Finalize session metadata and traces for one auto-extraction attempt."""
        result = dict(result or {})
        if success:
            self._update_pending_state(
                session_id,
                pending_message_count=0,
                touched=True,
                in_progress=False,
                task_id=task_id,
            )
            self._append_extraction_audit(
                event="memory_extract_completed",
                trigger="post_turn",
                session_id=session_id,
                project_slug=None,
                result=result,
                task_id=task_id,
            )
            self.session_store.append_conversation_event(
                session_id,
                event_kind="memory_extract_completed",
                role="system",
                content="Auto extraction completed: %s entries, %s conclusions."
                % (result.get("entries", 0), result.get("conclusions", 0)),
                payload={
                    "task_id": task_id,
                    "entries": result.get("entries", 0),
                    "conclusions": result.get("conclusions", 0),
                    "updated_files": list(result.get("updated_files") or []),
                },
            )
            return

        self._update_pending_state(session_id, in_progress=False, task_id=task_id)
        self._append_extraction_audit(
            event="memory_extract_failed",
            trigger="post_turn",
            session_id=session_id,
            error=error,
            task_id=task_id,
        )
        self.session_store.append_conversation_event(
            session_id,
            event_kind="memory_extract_failed",
            role="system",
            content="Auto extraction failed.",
            payload={"task_id": task_id, "error": error},
        )

    def _should_run_background_subagent(self) -> bool:
        """Return True when the dedicated LLM extraction worker should run off-thread."""
        if not bool(getattr(self.config.memory, "auto_extract_background", True)):
            return False
        return self.llm_extractor is not None and self.llm_extractor.can_use_llm()

    def submit_auto_extract(
        self,
        user_message: str,
        assistant_message: str,
        project_slug: Optional[str],
        session_id: str = "",
        pending_tool_calls: bool = False,
    ) -> Dict[str, object]:
        """Run or schedule lifecycle-driven post-turn extraction."""
        if self._should_run_background_subagent():
            job = self.auto_extract_executor.submit_post_turn(
                user_message=user_message,
                assistant_message=assistant_message,
                project_slug=project_slug,
                session_id=session_id,
                pending_tool_calls=pending_tool_calls,
            )
            if job is None:
                return {"queued": False, "entries": 0, "conclusions": 0, "updated_files": []}
            return {
                "queued": True,
                "task_id": job.task_id,
                "entries": 0,
                "conclusions": 0,
                "updated_files": [],
            }

        result = self.auto_extract(
            user_message,
            assistant_message,
            project_slug,
            session_id=session_id,
            pending_tool_calls=pending_tool_calls,
        )
        result["queued"] = False
        return result

    def collect_auto_extract_notifications(self, session_id: Optional[str] = None) -> List[str]:
        """Return completed background memory-update notifications."""
        return self.auto_extract_executor.collect_notifications(session_id=session_id)

    def wait_for_auto_extract_tasks(self, session_id: str, timeout_seconds: float = 0.0) -> None:
        """Wait briefly for background extraction tasks."""
        self.auto_extract_executor.wait_for_session(session_id, timeout_seconds=timeout_seconds)

    def auto_extract(
        self,
        user_message: str,
        assistant_message: str,
        project_slug: Optional[str],
        session_id: str = "",
        pending_tool_calls: bool = False,
    ) -> Dict[str, object]:
        """Run lifecycle-driven post-turn extraction into dynamic memory."""
        prepared = self.prepare_auto_extract_job(
            user_message=user_message,
            assistant_message=assistant_message,
            project_slug=project_slug,
            session_id=session_id,
            pending_tool_calls=pending_tool_calls,
        )
        if not prepared.get("should_run"):
            return {"entries": 0, "conclusions": 0, "updated_files": []}
        try:
            result = self.run_auto_extract_payload(dict(prepared.get("payload") or {}))
            self.complete_auto_extract_job(session_id=session_id, success=True, result=result)
            return result
        except Exception as exc:
            self.complete_auto_extract_job(session_id=session_id, success=False, error=str(exc))
            return {"entries": 0, "conclusions": 0, "updated_files": [], "error": str(exc)}

    def extract_pre_compress(self, *, session_id: str, project_slug: Optional[str], window_text: str) -> Dict[str, object]:
        """Salvage durable memory from context that is about to be compressed."""
        source_excerpt = shorten(window_text, 160)
        if str(self.session_store.get_session_meta(session_id).get("mode") or "").strip() == "research":
            self._append_extraction_audit(
                event="memory_extract_skipped",
                trigger="pre_compress",
                session_id=session_id,
                project_slug=project_slug,
                reason="research_mode_uses_research_log_archive",
                source_excerpt=source_excerpt,
            )
            return {"entries": 0, "conclusions": 0, "updated_files": []}
        if not self.config.memory.auto_extract:
            self._append_extraction_audit(
                event="memory_extract_skipped",
                trigger="pre_compress",
                session_id=session_id,
                project_slug=project_slug,
                reason="auto_extract_disabled",
                source_excerpt=source_excerpt,
            )
            return {"entries": 0, "conclusions": 0, "updated_files": []}
        if not str(window_text or "").strip():
            self._append_extraction_audit(
                event="memory_extract_skipped",
                trigger="pre_compress",
                session_id=session_id,
                project_slug=project_slug,
                reason="empty_window",
                source_excerpt=source_excerpt,
            )
            return {"entries": 0, "conclusions": 0, "updated_files": []}
        payload = self._build_payload(
            trigger="pre_compress",
            project_slug=project_slug,
            session_id=session_id,
            source_text=window_text,
        )
        self._append_extraction_audit(
            event="memory_extract_started",
            trigger="pre_compress",
            session_id=session_id,
            project_slug=project_slug,
            source_excerpt=source_excerpt,
        )
        try:
            result = self.run_auto_extract_payload(payload)
        except Exception as exc:
            self._append_extraction_audit(
                event="memory_extract_failed",
                trigger="pre_compress",
                session_id=session_id,
                project_slug=project_slug,
                error=str(exc),
                source_excerpt=source_excerpt,
            )
            return {"entries": 0, "conclusions": 0, "updated_files": [], "error": str(exc)}
        self._append_extraction_audit(
            event="memory_extract_completed",
            trigger="pre_compress",
            session_id=session_id,
            project_slug=project_slug,
            result=result,
            source_excerpt=source_excerpt,
        )
        return result

    def extract_session_end(self, *, session_id: str, project_slug: Optional[str]) -> Dict[str, object]:
        """Consolidate durable memory at session end."""
        if str(self.session_store.get_session_meta(session_id).get("mode") or "").strip() == "research":
            self._append_extraction_audit(
                event="memory_extract_skipped",
                trigger="session_end",
                session_id=session_id,
                project_slug=project_slug,
                reason="research_mode_uses_research_log_archive",
            )
            return {"entries": 0, "conclusions": 0, "updated_files": []}
        if not self.config.memory.auto_extract:
            self._append_extraction_audit(
                event="memory_extract_skipped",
                trigger="session_end",
                session_id=session_id,
                project_slug=project_slug,
                reason="auto_extract_disabled",
            )
            return {"entries": 0, "conclusions": 0, "updated_files": []}
        messages = self.session_store.get_all_messages(session_id)
        if not messages:
            self._append_extraction_audit(
                event="memory_extract_skipped",
                trigger="session_end",
                session_id=session_id,
                project_slug=project_slug,
                reason="no_messages",
            )
            return {"entries": 0, "conclusions": 0, "updated_files": []}
        lines = []
        for item in messages[-20:]:
            lines.append("[%s] %s" % (item["role"], item["content"]))
        source_text = "\n".join(lines)
        payload = self._build_payload(
            trigger="session_end",
            project_slug=project_slug,
            session_id=session_id,
            source_text=source_text,
        )
        self._append_extraction_audit(
            event="memory_extract_started",
            trigger="session_end",
            session_id=session_id,
            project_slug=project_slug,
            source_excerpt=source_text,
        )
        try:
            result = self.run_auto_extract_payload(payload)
        except Exception as exc:
            self._append_extraction_audit(
                event="memory_extract_failed",
                trigger="session_end",
                session_id=session_id,
                project_slug=project_slug,
                error=str(exc),
                source_excerpt=source_text,
            )
            return {"entries": 0, "conclusions": 0, "updated_files": [], "error": str(exc)}
        self._append_extraction_audit(
            event="memory_extract_completed",
            trigger="session_end",
            session_id=session_id,
            project_slug=project_slug,
            result=result,
            source_excerpt=source_text,
        )
        return result

    def prepare_context(self, query: str, project_slug: str, session_id: str) -> ContextBundle:
        """Prepare a retrieval bundle for a query."""
        memory_index_lines = self.paths.memory_index_file.read_text(encoding="utf-8").splitlines()
        memory_index = "\n".join(memory_index_lines[: self.config.memory.max_index_lines]).strip()
        project_context_summary = shorten(
            self.dynamic_store.read_file("project-context", project_slug=project_slug),
            512,
        )
        return ContextBundle(
            static_rules=self.static_store.load_global_rules(),
            core_config=self.paths.core_config_file.read_text(encoding="utf-8") if self.paths.core_config_file.exists() else "",
            project_rules=self.static_store.load_project_rules(project_slug),
            memory_index=memory_index,
            project_context_summary=project_context_summary,
            recent_messages=self.session_store.get_recent_messages(
                session_id=session_id,
                limit=self.config.memory.recent_message_limit,
            ),
        )

    def _search_research_state_artifacts(self, query: str, project_slug: Optional[str], limit: int = 5) -> List[Dict[str, object]]:
        """Search append-only research_log records for retrieval and context injection."""
        try:
            indexed = self.research_index.search(
                query=query,
                project_slug=project_slug,
                limit=limit,
            )
            if indexed:
                return indexed
        except Exception:
            pass
        rows: List[Dict[str, object]] = []
        projects = []
        if project_slug:
            projects = [str(project_slug)]
        elif self.paths.projects_dir.exists():
            projects = [item.name for item in sorted(self.paths.projects_dir.iterdir()) if item.is_dir()]
        for slug in projects:
            records_path = self.paths.project_research_log_file(slug)
            if not records_path.exists():
                continue
            for item in read_jsonl(records_path):
                if not isinstance(item, dict):
                    continue
                content = str(item.get("content") or "")
                record_type = str(item.get("type") or "research_note")
                blob = "\n".join(
                    part
                    for part in [
                        record_type,
                        str(item.get("title") or ""),
                        content,
                    ]
                    if part
                )
                score = overlap_score(query, blob)
                if score <= 0:
                    continue
                rows.append(
                    {
                        "id": str(item.get("id") or ""),
                        "artifact_type": record_type,
                        "title": str(item.get("title") or ""),
                        "summary": shorten(content, 240),
                        "content_inline": content,
                        "content_path": self.paths.project_research_log_file(slug).relative_to(self.paths.home).as_posix(),
                        "stage": str(item.get("stage") or ""),
                        "focus_activity": str(item.get("focus_activity") or ""),
                        "status": "",
                        "review_status": "",
                        "project_slug": slug,
                        "session_id": str(item.get("session_id") or ""),
                        "tags": [record_type],
                        "related_ids": [],
                        "next_action": "",
                        "metadata": {
                            "source_type": "research_log",
                            "record_type": record_type,
                            "source_refs": list(item.get("source_refs") or []),
                            "raw_text": content,
                        },
                        "created_at": str(item.get("created_at") or ""),
                        "score": float(score),
                    }
                )
        rows.sort(
            key=lambda item: (
                -float(item.get("score") or 0.0),
                parse_utc_timestamp(str(item.get("created_at") or "")),
            ),
        )
        return rows[: max(1, int(limit or 5))]

    def _resolve_projects_for_research_search(self, project_slug: Optional[str]) -> List[str]:
        """Return the project set that should participate in research retrieval."""
        if project_slug:
            return [str(project_slug)]
        if self.paths.projects_dir.exists():
            return [item.name for item in sorted(self.paths.projects_dir.iterdir()) if item.is_dir()]
        return []

    def _search_research_channels(
        self,
        *,
        query: str,
        project_slug: Optional[str],
        channels: Sequence[str],
        channel_mode: str = "search",
        limit_per_channel: int = 3,
    ) -> List[Dict[str, object]]:
        """Recover research records through selected research-log types."""
        selected_channels: List[str] = []
        for channel in channels:
            canonical = normalize_research_channel_name(str(channel))
            if canonical and canonical not in selected_channels:
                selected_channels.append(canonical)
        if not selected_channels:
            return []

        mode = str(channel_mode or "search").strip().lower()
        if mode not in {"search", "recent", "all"}:
            mode = "search"
        per_channel = max(1, min(int(limit_per_channel or 3), 20))
        try:
            indexed = self.research_index.search(
                query=query,
                project_slug=project_slug,
                channels=selected_channels,
                channel_mode=mode,
                limit_per_channel=per_channel,
            )
            if indexed or mode in {"recent", "all"}:
                return indexed
        except Exception:
            pass
        rows: List[Dict[str, object]] = []
        for slug in self._resolve_projects_for_research_search(project_slug):
            log_rows = [
                item for item in read_jsonl(self.paths.project_research_log_file(slug))
                if isinstance(item, dict) and str(item.get("type") or "research_note") in selected_channels
            ]
            for channel in selected_channels:
                ranked: List[Dict[str, object]] = []
                for item in log_rows:
                    record_type = str(item.get("type") or "research_note")
                    if record_type != channel:
                        continue
                    raw_content = str(item.get("content") or "")
                    title = str(item.get("title") or "").strip() or "%s record" % record_type
                    summary = shorten(raw_content, 240)
                    blob = "\n".join(part for part in [title, record_type, summary, raw_content] if part)
                    if mode == "search":
                        score = overlap_score(query, blob) if str(query or "").strip() else 0.0
                        if score <= 0:
                            continue
                    else:
                        score = 1.0
                    ranked.append(
                        {
                            "id": str(item.get("id") or item.get("created_at") or ""),
                            "artifact_type": record_type,
                            "title": title,
                            "summary": summary,
                            "content_inline": raw_content,
                            "content_path": self.paths.project_research_log_file(slug).relative_to(self.paths.home).as_posix(),
                            "stage": "",
                            "focus_activity": "",
                            "status": "",
                            "review_status": "",
                            "project_slug": slug,
                            "session_id": str(item.get("session_id") or ""),
                            "tags": [record_type],
                            "related_ids": [],
                            "next_action": "",
                            "metadata": {
                                "channel": record_type,
                                "source_type": "research_log",
                                "record_type": record_type,
                                "source_refs": list(item.get("source_refs") or []),
                            },
                            "created_at": str(item.get("created_at") or ""),
                            "score": float(score),
                            "channel": record_type,
                        }
                    )
                if mode == "search":
                    ranked.sort(
                        key=lambda entry: (
                            -float(entry.get("score") or 0.0),
                            parse_utc_timestamp(str(entry.get("created_at") or "")),
                        ),
                    )
                    rows.extend(ranked[:per_channel])
                else:
                    ranked.sort(
                        key=lambda entry: parse_utc_timestamp(str(entry.get("created_at") or "")),
                        reverse=True,
                    )
                    rows.extend(ranked[: per_channel if mode == "recent" else per_channel * 4])
        if mode == "search":
            rows.sort(
                key=lambda entry: (
                    -float(entry.get("score") or 0.0),
                    parse_utc_timestamp(str(entry.get("created_at") or "")),
                ),
            )
        else:
            rows.sort(
                key=lambda entry: parse_utc_timestamp(str(entry.get("created_at") or "")),
                reverse=True,
            )
        return rows

    def query_memory_sources(
        self,
        query: str,
        project_slug: Optional[str],
        session_id: str,
        *,
        research_channels: Optional[Sequence[str]] = None,
        research_channel_mode: str = "search",
        limit_per_channel: int = 3,
    ) -> Dict[str, object]:
        """Return raw retrieval hits across implemented memory layers."""
        selected_channels = [str(channel) for channel in list(research_channels or []) if str(channel).strip()]
        research_hits = (
            self._search_research_channels(
                query=query,
                project_slug=project_slug,
                channels=selected_channels,
                channel_mode=research_channel_mode,
                limit_per_channel=limit_per_channel,
            )
            if selected_channels
            else self._search_research_state_artifacts(query=query, project_slug=project_slug, limit=5)
        )
        return {
            "dynamic_hits": self.dynamic_store.search(query=query, project_slug=project_slug, limit=5),
            "session_hits": self.session_store.search_messages(
                query=query,
                limit=self.config.memory.session_search_limit,
                project_slug=project_slug,
            ),
            "event_hits": self.session_store.search_conversation_events(
                query=query,
                limit=self.config.memory.session_search_limit,
                project_slug=project_slug,
            ),
            "knowledge_hits": self.knowledge_store.search(
                query=query,
                limit=self.config.memory.knowledge_search_limit,
                project_slug=project_slug,
            ),
            "research_hits": research_hits,
            "graph_hits": [],
        }

    def review(self, project_slug: Optional[str] = None) -> ReviewReport:
        """Review current dynamic memory."""
        return build_review_report(
            self.dynamic_store.list_entries(project_slug=project_slug),
            stale_days=self.config.memory.review_stale_days,
        )

    def promote(self, slug: str) -> str:
        """Promote a dynamic memory entry into static rules."""
        entry = self.dynamic_store.get_entry(slug)
        if entry is None:
            raise KeyError(slug)
        target_project = entry.project_slug if entry.file_alias.startswith("project-") else None
        return self.static_store.promote_summary(entry.summary or entry.body, project_slug=target_project)
