"""Session tools for Moonshine."""

from __future__ import annotations

import gzip
import json

from moonshine.utils import read_jsonl, read_text, shorten


def list_sessions(runtime: dict, limit: int = 10) -> dict:
    """List recent sessions."""
    return {"sessions": runtime["session_store"].list_sessions(limit=limit)}


def search_sessions(runtime: dict, query: str, project_slug: str = "") -> dict:
    """Search session messages."""
    return {"results": runtime["session_store"].search_messages(query, project_slug=project_slug or None, limit=5)}


def query_session_records(runtime: dict, query: str, session_id: str = "", limit: int = 8) -> dict:
    """Search complete raw session records and return source locations."""
    paths = runtime["paths"]
    target_session = str(session_id or runtime.get("session_id") or "").strip()
    if not target_session:
        raise ValueError("session_id is required when there is no active runtime session")
    session_dir = paths.session_dir(target_session)
    if not session_dir.exists():
        raise ValueError("session not found: %s" % target_session)
    needle = str(query or "").strip().lower()
    if not needle:
        raise ValueError("query cannot be empty")

    max_hits = max(1, int(limit or 8))
    hits = []

    def add_hit(source: str, relative_path: str, line: int, text: str) -> None:
        if len(hits) >= max_hits:
            return
        haystack = str(text or "")
        if needle not in haystack.lower():
            return
        hits.append(
            {
                "source": source,
                "path": relative_path,
                "line": line,
                "excerpt": shorten(haystack.replace("\n", " "), 600),
            }
        )

    plain_files = [
        ("messages", paths.session_messages_file(target_session)),
        ("transcript", paths.session_transcript_file(target_session)),
        ("tool_events", paths.session_tool_events_file(target_session)),
        ("turn_events", paths.session_turn_events_file(target_session)),
        ("provider_rounds_index", paths.session_provider_rounds_file(target_session)),
        ("context_summaries", paths.session_context_summaries_file(target_session)),
    ]
    for source, path in plain_files:
        if not path.exists():
            continue
        relative = path.relative_to(paths.home).as_posix()
        if path.suffix == ".jsonl":
            for line_no, item in enumerate(read_jsonl(path), start=1):
                add_hit(source, relative, line_no, json.dumps(item, ensure_ascii=False))
                if len(hits) >= max_hits:
                    break
        else:
            for line_no, line in enumerate(read_text(path).splitlines(), start=1):
                add_hit(source, relative, line_no, line)
                if len(hits) >= max_hits:
                    break
        if len(hits) >= max_hits:
            break

    archive_dir = paths.session_provider_round_archives_dir(target_session)
    if len(hits) < max_hits and archive_dir.exists():
        for archive_path in sorted(archive_dir.glob("*.json.gz")):
            try:
                with gzip.open(archive_path, "rt", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, ValueError):
                continue
            relative = archive_path.relative_to(paths.home).as_posix()
            add_hit("provider_round_archive", relative, 1, json.dumps(payload, ensure_ascii=False))
            if len(hits) >= max_hits:
                break

    return {
        "session_id": target_session,
        "query": query,
        "hits": hits,
        "raw_record_locations": {
            "messages": paths.session_messages_file(target_session).relative_to(paths.home).as_posix(),
            "transcript": paths.session_transcript_file(target_session).relative_to(paths.home).as_posix(),
            "tool_events": paths.session_tool_events_file(target_session).relative_to(paths.home).as_posix(),
            "provider_rounds_index": paths.session_provider_rounds_file(target_session).relative_to(paths.home).as_posix(),
            "provider_round_archives": paths.session_provider_round_archives_dir(target_session).relative_to(paths.home).as_posix(),
            "context_summaries": paths.session_context_summaries_file(target_session).relative_to(paths.home).as_posix(),
        },
    }
