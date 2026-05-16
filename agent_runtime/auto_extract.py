"""Background auto-extraction runner for Moonshine memory."""

from __future__ import annotations

import threading
import traceback
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from moonshine.utils import utc_now


STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


@dataclass
class MemoryExtractionJob:
    """Tracked execution of one background memory extraction task."""

    task_id: str
    session_id: str
    project_slug: str
    trigger: str
    status: str = STATUS_QUEUED
    created_at: str = field(default_factory=utc_now)
    completed_at: str = ""
    result: Dict[str, object] = field(default_factory=dict)
    error: str = ""
    notification: str = ""
    notified: bool = False


def _pretty_path(path_text: str) -> str:
    """Render local paths with a stable, human-friendly home prefix."""
    if not path_text:
        return ""
    path = Path(path_text)
    try:
        home = Path.home()
        return "~/%s" % path.resolve().relative_to(home.resolve()).as_posix()
    except Exception:
        return str(path_text)


def _build_notification(result: Dict[str, object]) -> str:
    """Build the terminal notification for a completed memory update."""
    files = [str(item) for item in list(result.get("updated_files") or []) if str(item)]
    if not files:
        return ""
    display_path = _pretty_path(files[0])
    extra_count = max(0, len(files) - 1)
    if extra_count:
        display_path = "%s (+%s more)" % (display_path, extra_count)
    return "Memory updated in %s - /memory to edit" % display_path


class BackgroundMemoryExtractionExecutor(object):
    """Run memory extraction in a small isolated background worker pool.

    This intentionally mirrors the useful parts of DeerFlow's subagent executor:
    task ids, explicit statuses, bounded background execution, and non-blocking
    result polling. Unlike a general subagent, this runner never receives tools
    and only calls Moonshine's memory extraction pipeline.
    """

    def __init__(self, memory_manager, max_workers: int = 1):
        self.memory_manager = memory_manager
        self.executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers or 1)),
            thread_name_prefix="moonshine-memory-extract-",
        )
        self.lock = threading.Lock()
        self.jobs: Dict[str, MemoryExtractionJob] = {}
        self.futures: Dict[str, Future] = {}

    def has_active_session_job(self, session_id: str) -> bool:
        """Return True when a non-terminal job is already running for a session."""
        with self.lock:
            return any(
                job.session_id == session_id and job.status in {STATUS_QUEUED, STATUS_RUNNING}
                for job in self.jobs.values()
            )

    def submit_post_turn(
        self,
        *,
        user_message: str,
        assistant_message: str,
        project_slug: Optional[str],
        session_id: str,
        pending_tool_calls: bool = False,
    ) -> Optional[MemoryExtractionJob]:
        """Prepare and submit one post-turn extraction job if trigger conditions pass."""
        if self.has_active_session_job(session_id):
            self.memory_manager.note_auto_extract_deferred(session_id=session_id, increment=2)
            return None

        prepared = self.memory_manager.prepare_auto_extract_job(
            user_message=user_message,
            assistant_message=assistant_message,
            project_slug=project_slug,
            session_id=session_id,
            pending_tool_calls=pending_tool_calls,
        )
        if not prepared.get("should_run"):
            return None

        task_id = "memory-%s" % uuid.uuid4().hex[:10]
        job = MemoryExtractionJob(
            task_id=task_id,
            session_id=session_id,
            project_slug=project_slug or "",
            trigger=str(prepared.get("trigger") or "post_turn"),
        )
        with self.lock:
            self.jobs[task_id] = job
            self.futures[task_id] = self.executor.submit(self._run_job, task_id, dict(prepared.get("payload") or {}))
        return job

    def _run_job(self, task_id: str, payload: Dict[str, object]) -> None:
        """Run one extraction job and persist its terminal status."""
        with self.lock:
            job = self.jobs.get(task_id)
            if job is None:
                return
            job.status = STATUS_RUNNING

        try:
            result = self.memory_manager.run_auto_extract_payload(payload)
            self.memory_manager.complete_auto_extract_job(
                session_id=str(payload.get("session_id", "")),
                task_id=task_id,
                success=True,
                result=result,
            )
            with self.lock:
                job = self.jobs.get(task_id)
                if job is not None:
                    job.status = STATUS_COMPLETED
                    job.completed_at = utc_now()
                    job.result = dict(result)
                    job.notification = _build_notification(result)
        except Exception as exc:
            error_text = "%s\n%s" % (exc, traceback.format_exc(limit=4))
            self.memory_manager.complete_auto_extract_job(
                session_id=str(payload.get("session_id", "")),
                task_id=task_id,
                success=False,
                error=error_text,
            )
            with self.lock:
                job = self.jobs.get(task_id)
                if job is not None:
                    job.status = STATUS_FAILED
                    job.completed_at = utc_now()
                    job.error = error_text

    def collect_notifications(self, session_id: Optional[str] = None) -> List[str]:
        """Return newly completed terminal notifications and mark them as delivered."""
        notifications = []
        cleanup_ids = []
        with self.lock:
            for task_id, job in self.jobs.items():
                if session_id and job.session_id != session_id:
                    continue
                if job.status == STATUS_COMPLETED and not job.notified:
                    if job.notification:
                        notifications.append(job.notification)
                    job.notified = True
                    cleanup_ids.append(task_id)
                elif job.status == STATUS_FAILED and not job.notified:
                    notifications.append("Memory extraction failed; run /memory to inspect current files.")
                    job.notified = True
                    cleanup_ids.append(task_id)

            for task_id in cleanup_ids:
                future = self.futures.get(task_id)
                if future is None or future.done():
                    self.jobs.pop(task_id, None)
                    self.futures.pop(task_id, None)
        return notifications

    def wait_for_session(self, session_id: str, timeout_seconds: float = 0.0) -> None:
        """Wait briefly for active session jobs, primarily for graceful shutdown/tests."""
        futures = []
        with self.lock:
            for task_id, job in self.jobs.items():
                if job.session_id == session_id:
                    future = self.futures.get(task_id)
                    if future is not None:
                        futures.append(future)
        if futures:
            wait(futures, timeout=max(0.0, float(timeout_seconds or 0.0)))
