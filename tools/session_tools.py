"""Session tools for Moonshine."""

from __future__ import annotations

import json
from typing import Dict, List, Tuple

from moonshine.storage.session_store import RETRIEVAL_TOOL_EVENT_NAMES
from moonshine.utils import estimate_token_count, overlap_score, read_jsonl, shorten


TOOL_RESULT_VISIBLE_TOKEN_BUDGET = 20000


def _json_text(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _tool_event_search_text(event: Dict[str, object]) -> str:
    """Render one tool event as the original searchable tool-result payload."""
    return _json_text(
        {
            "tool": event.get("tool", ""),
            "call_id": event.get("call_id", ""),
            "arguments": event.get("arguments", {}),
            "output": event.get("output", {}),
            "error": event.get("error"),
            "tool_round": event.get("tool_round", ""),
            "created_at": event.get("created_at", ""),
        }
    )


def _tool_event_score(query: str, event: Dict[str, object], rendered: str) -> float:
    """Score tool events with simple exact-match, overlap, error, and recency signals."""
    lowered = rendered.lower()
    normalized_query = str(query or "").strip().lower()
    score = overlap_score(query, rendered)
    if normalized_query and normalized_query in lowered:
        score += 1.0
    if event.get("error"):
        score += 0.15
    return score


def _trim_tool_result_text(text: str, *, query: str, token_budget: int) -> str:
    """Hard-trim matched tool-result text around the strongest query anchor."""
    source = str(text or "")
    if token_budget <= 0 or not source:
        return ""
    if estimate_token_count(source) <= token_budget:
        return source
    char_budget = max(256, int(token_budget) * 4)
    lower_source = source.lower()
    anchors = [str(query or "").strip()]
    anchors.extend(part for part in str(query or "").replace("_", " ").split() if len(part) >= 3)
    match_index = -1
    for anchor in anchors:
        if not anchor:
            continue
        match_index = lower_source.find(anchor.lower())
        if match_index >= 0:
            break
    if match_index < 0:
        match_index = 0
    start = max(0, match_index - char_budget // 2)
    end = min(len(source), start + char_budget)
    start = max(0, end - char_budget)
    prefix = "[truncated before matched tool_result context]\n" if start > 0 else ""
    suffix = "\n[truncated after matched tool_result context]" if end < len(source) else ""
    return prefix + source[start:end].strip() + suffix


def _matched_tool_results(
    *,
    query: str,
    relative_path: str,
    matches: List[Dict[str, object]],
    limit: int,
) -> Tuple[List[Dict[str, object]], Dict[str, object]]:
    """Return indexed tool-result matches as complete units under a token cap."""
    ranked: List[Tuple[float, int, Dict[str, object], str]] = []
    for line_no, event in enumerate(matches, start=1):
        if not isinstance(event, dict):
            continue
        tool_name = str(event.get("tool") or "").strip()
        if tool_name in RETRIEVAL_TOOL_EVENT_NAMES:
            continue
        rendered = str(event.get("_search_text") or "") or _tool_event_search_text(event)
        score = _tool_event_score(query, event, rendered)
        if score <= 0:
            continue
        ranked.append((score, line_no, dict(event), rendered))
    ranked.sort(key=lambda item: (item[0], str(item[2].get("created_at") or "")), reverse=True)

    selected = ranked[: max(1, int(limit or 8))]
    full_blocks = []
    manifest_results = []
    for score, index_rank, event, rendered in selected:
        event_id = str(event.get("id") or event.get("event_id") or event.get("call_id") or "tool-event-rank-%s" % index_rank)
        archive_path = str(event.get("archive_path") or "")
        block = (
            "## Tool Result Match\n"
            "event_id: {event_id}\n"
            "source_path: {path}\n"
            "archive_path: {archive_path}\n"
            "index_rank: {index_rank}\n"
            "score: {score:.4f}\n\n"
            "```json\n{payload}\n```"
        ).format(
            event_id=event_id,
            path=relative_path,
            archive_path=archive_path or "(inline legacy record)",
            index_rank=index_rank,
            score=float(score),
            payload=rendered,
        )
        full_blocks.append(block)
        manifest_results.append(
            {
                "event_id": event_id,
                "tool": str(event.get("tool") or ""),
                "created_at": str(event.get("created_at") or ""),
                "index_rank": index_rank,
                "score": float(score),
                "source_path": relative_path,
                "archive_path": archive_path,
            }
        )

    kept_blocks = []
    remaining = TOOL_RESULT_VISIBLE_TOKEN_BUDGET
    truncated = False
    for block in full_blocks:
        if remaining <= 0:
            truncated = True
            break
        cost = estimate_token_count(block)
        if cost <= remaining:
            kept_blocks.append(block)
            remaining -= cost
            continue
        if remaining >= 64:
            kept_blocks.append(_trim_tool_result_text(block, query=query, token_budget=remaining))
        truncated = True
        break
    content = "\n\n".join(kept_blocks)
    return manifest_results, {
        "content": content,
        "content_mode": "truncated_complete_tool_result" if truncated else "full_complete_tool_result",
        "token_budget": TOOL_RESULT_VISIBLE_TOKEN_BUDGET,
        "source_unit": "complete_tool_result",
        "excluded_tools": sorted(RETRIEVAL_TOOL_EVENT_NAMES),
        "matched_count": len(selected),
        "truncated": truncated,
    }


def list_sessions(runtime: dict, limit: int = 10) -> dict:
    """List recent sessions."""
    max_sessions = min(100, max(1, int(limit or 10)))
    return {"sessions": runtime["session_store"].list_sessions(limit=max_sessions)}


def search_sessions(runtime: dict, query: str, project_slug: str = "") -> dict:
    """Search session messages."""
    return {"results": runtime["session_store"].search_messages(query, project_slug=project_slug or None, limit=5)}


def query_session_records(runtime: dict, query: str, session_id: str = "", limit: int = 8) -> dict:
    """Search unified raw session records and return source locations."""
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

    max_hits = min(50, max(1, int(limit or 8)))
    session_store = runtime["session_store"]
    record_hits = session_store.search_session_records(query, limit=max_hits, session_id=target_session)

    def render_record(record: Dict[str, object]) -> str:
        metadata = dict(record.get("metadata") or {})
        lines = [
            "[%s/%s] %s"
            % (
                str(record.get("record_type") or "record"),
                str(record.get("role") or ""),
                str(record.get("title") or ""),
            ),
            "record_id: %s" % str(record.get("record_id") or ""),
            "created_at: %s" % str(record.get("created_at") or ""),
        ]
        if metadata.get("source"):
            lines.append("source: %s" % str(metadata.get("source") or ""))
        if metadata.get("archive_path"):
            lines.append("archive_path: %s" % str(metadata.get("archive_path") or ""))
        lines.append(str(record.get("content") or ""))
        return "\n".join(line for line in lines if line)

    def render_window(records: List[Dict[str, object]]) -> str:
        return "\n\n".join(render_record(record) for record in records if isinstance(record, dict))

    results = []
    remaining_tokens = TOOL_RESULT_VISIBLE_TOKEN_BUDGET
    truncated = False
    for hit in record_hits:
        if remaining_tokens <= 0:
            truncated = True
            break
        window = session_store.get_session_record_window(
            target_session,
            str(hit.get("record_id") or ""),
            before=4,
            after=6,
        )
        local_context = render_window(window)
        content = str(hit.get("content") or "")
        item = {
            "record_id": str(hit.get("record_id") or ""),
            "type": str(hit.get("record_type") or "record"),
            "title": str(hit.get("title") or ""),
            "content": content,
            "local_context": local_context,
            "score": float(hit.get("score") or 0.0),
            "source_refs": {
                "session_id": target_session,
                "archive_path": str(dict(hit.get("metadata") or {}).get("archive_path") or ""),
            },
        }
        cost = estimate_token_count(json.dumps(item, ensure_ascii=False), model_name="")
        if cost > remaining_tokens:
            item["local_context"] = _trim_tool_result_text(local_context, query=query, token_budget=max(64, remaining_tokens // 2))
            item["content"] = _trim_tool_result_text(content, query=query, token_budget=max(64, remaining_tokens // 2))
            cost = estimate_token_count(json.dumps(item, ensure_ascii=False), model_name="")
            truncated = True
        if cost > remaining_tokens:
            item["local_context"] = ""
            item["content"] = _trim_tool_result_text(content or local_context, query=query, token_budget=max(64, remaining_tokens - 128))
            cost = estimate_token_count(json.dumps(item, ensure_ascii=False), model_name="")
            truncated = True
        if cost <= remaining_tokens or not results:
            results.append(item)
            remaining_tokens = max(0, remaining_tokens - min(cost, remaining_tokens))
        if len(results) >= max_hits:
            break

    retrieval_tool_refs = []
    tool_events_path = paths.session_tool_events_file(target_session)
    if tool_events_path.exists():
        relative_tool_events = tool_events_path.relative_to(paths.home).as_posix()
        for index, manifest in enumerate(read_jsonl(tool_events_path), start=1):
            if not isinstance(manifest, dict):
                continue
            tool_name = str(manifest.get("tool") or "").strip()
            if tool_name not in RETRIEVAL_TOOL_EVENT_NAMES:
                continue
            searchable = _json_text(
                {
                    "tool": tool_name,
                    "call_id": manifest.get("call_id", ""),
                    "arguments_preview": manifest.get("arguments_preview", ""),
                    "status": manifest.get("status", ""),
                    "error": manifest.get("error"),
                    "created_at": manifest.get("created_at", ""),
                    "archive_path": manifest.get("archive_path", ""),
                }
            )
            if needle not in searchable.lower() and overlap_score(query, searchable) <= 0:
                continue
            retrieval_tool_refs.append(
                {
                    "tool": tool_name,
                    "call_id": str(manifest.get("call_id") or ""),
                    "event_id": str(manifest.get("event_id") or ""),
                    "created_at": str(manifest.get("created_at") or ""),
                    "status": str(manifest.get("status") or ""),
                    "source_path": relative_tool_events,
                    "line": index,
                    "archive_path": str(manifest.get("archive_path") or ""),
                    "payload_sha256": str(manifest.get("payload_sha256") or ""),
                    "arguments_preview": str(manifest.get("arguments_preview") or ""),
                }
            )
            if len(retrieval_tool_refs) >= max_hits:
                break

    return {
        "session_id": target_session,
        "query": query,
        "results": results,
        "recovery_refs": {
            "retrieval_tool_refs": retrieval_tool_refs,
            "result_token_budget": TOOL_RESULT_VISIBLE_TOKEN_BUDGET,
            "truncated": truncated,
        },
        "raw_record_locations": {
            "messages": paths.session_messages_file(target_session).relative_to(paths.home).as_posix(),
            "transcript": paths.session_transcript_file(target_session).relative_to(paths.home).as_posix(),
            "tool_events": paths.session_tool_events_file(target_session).relative_to(paths.home).as_posix(),
            "tool_event_archives": paths.session_tool_event_archives_dir(target_session).relative_to(paths.home).as_posix(),
            "provider_rounds_index": paths.session_provider_rounds_file(target_session).relative_to(paths.home).as_posix(),
            "provider_round_archives": paths.session_provider_round_archives_dir(target_session).relative_to(paths.home).as_posix(),
            "context_summaries": paths.session_context_summaries_file(target_session).relative_to(paths.home).as_posix(),
            "session_index": paths.sessions_db.relative_to(paths.home).as_posix(),
        },
    }
