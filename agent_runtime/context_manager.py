"""Context loading, retrieval, and compression for Moonshine."""

from __future__ import annotations

import json
import math
from typing import Dict, List, Optional, Sequence, Tuple

from moonshine.agent_runtime.model_metadata import resolve_model_context_window
from moonshine.agent_runtime.memory_provider import ContextBundle
from moonshine.agent_runtime.research_log import ResearchLogStore, normalize_research_log_type
from moonshine.providers import OfflineProvider
from moonshine.utils import (
    append_jsonl,
    estimate_structured_token_count,
    estimate_token_count,
    overlap_score,
    parse_utc_timestamp,
    read_jsonl,
    read_text,
    shorten,
    split_text_by_token_budget,
    utc_now,
)


SUMMARY_PROVIDER_INPUT_TOKEN_BUDGET = 500000


class ContextManager(object):
    """Implement startup context loading and on-demand retrieval."""

    def __init__(self, *, paths, config, provider, memory_manager, session_store, tool_manager):
        self.paths = paths
        self.config = config
        self.provider = provider
        self.memory_manager = memory_manager
        self.session_store = session_store
        self.tool_manager = tool_manager
        self.research_log = ResearchLogStore(
            paths,
            knowledge_store=getattr(memory_manager, "knowledge_store", None) if memory_manager is not None else None,
        )

    def _token_model_name(self) -> str:
        """Return the best available model name for token estimation."""
        provider_model = str(getattr(self.provider, "model", "") or "").strip()
        if provider_model:
            return provider_model
        return str(getattr(self.config.provider, "model", "") or "").strip()

    def estimate_tokens(self, text: str) -> int:
        """Estimate tokens, preferring tiktoken when it is available."""
        return estimate_token_count(text or "", model_name=self._token_model_name())

    def estimate_message_tokens(self, messages: Sequence[Dict[str, object]]) -> int:
        """Estimate message tokens."""
        total = 0
        for item in messages:
            total += 6
            total += estimate_structured_token_count(item.get("content", ""), model_name=self._token_model_name())
            if item.get("reasoning_content"):
                total += estimate_structured_token_count(item.get("reasoning_content", ""), model_name=self._token_model_name())
            if item.get("tool_calls"):
                total += estimate_structured_token_count(item.get("tool_calls"), model_name=self._token_model_name())
            if item.get("tool_call_id"):
                total += estimate_structured_token_count(item.get("tool_call_id", ""), model_name=self._token_model_name())
        return total

    def estimate_tool_schema_tokens(self, tool_schemas: Sequence[Dict[str, object]]) -> int:
        """Estimate the token cost of tool schemas sent with the request."""
        total = 0
        for item in tool_schemas or []:
            total += 4
            total += estimate_structured_token_count(item, model_name=self._token_model_name())
        return total

    def estimate_request_tokens(
        self,
        *,
        system_prompt: str,
        messages: Sequence[Dict[str, object]],
        tool_schemas: Optional[Sequence[Dict[str, object]]] = None,
    ) -> int:
        """Estimate the total request size including tool schemas."""
        return (
            self.estimate_tokens(system_prompt)
            + self.estimate_message_tokens(messages)
            + self.estimate_tool_schema_tokens(tool_schemas or [])
        )

    def _stringify_payload(self, value: object) -> str:
        """Render arbitrary payloads as compact text."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            return str(value)

    def _render_message_line(self, item: Dict[str, object]) -> str:
        """Render one provider message as readable text."""
        provider_like = {
            "role": str(item.get("role", "unknown")),
        }
        reasoning_content = self._stringify_payload(item.get("reasoning_content", "")).strip()
        if reasoning_content:
            provider_like["reasoning_content"] = reasoning_content
        provider_like["content"] = self._stringify_payload(item.get("content", "")).strip()
        tool_calls = list(item.get("tool_calls") or [])
        if tool_calls:
            provider_like["tool_calls"] = tool_calls
        return json.dumps(provider_like, ensure_ascii=False)

    def _trim_text_to_budget(self, text: str, token_budget: int) -> str:
        """Trim text by approximate token budget while preserving line structure."""
        if token_budget <= 0:
            return ""
        if self.estimate_tokens(text) <= token_budget:
            return text.strip()

        lines = [line.rstrip() for line in (text or "").splitlines()]
        kept: List[str] = []
        used = 0
        for line in lines:
            line_tokens = self.estimate_tokens(line) + 1
            if kept and used + line_tokens > token_budget:
                break
            if not kept and line_tokens > token_budget:
                kept.append(shorten(line, max(32, token_budget * 4)))
                used = token_budget
                break
            kept.append(line)
            used += line_tokens
        if len(kept) < len(lines):
            kept.append("... [truncated]")
        return "\n".join(line for line in kept if line).strip()

    def _summarize_bounded_text_with_provider(self, *, purpose: str, text: str, token_budget: int) -> str:
        """Ask the configured provider for one already-bounded source summary."""
        source = str(text or "").strip()
        if not source:
            return ""
        if self.estimate_tokens(source) <= token_budget:
            return source

        if not isinstance(self.provider, OfflineProvider):
            try:
                response = self.provider.generate(
                    system_prompt=(
                        "You compress context for Moonshine.\n"
                        "- Write like a compact research progress report for a later continuation round.\n"
                        "- Preserve important scientific and mathematical research details, not only status labels.\n"
                        "- Keep precise claims, hypotheses, definitions, proof ideas, counterexamples, failed paths, verifier objections, formulas, branch decisions, open questions, and next checks when they matter.\n"
                        "- Preserve the details of research progress so a later model can continue the work accurately.\n"
                        "- The output must stay within roughly %s tokens.\n"
                        "- Keep source attribution cues when available.\n"
                        "- Return concise markdown bullets only.\n"
                    )
                    % int(token_budget),
                    messages=[
                        {
                            "role": "user",
                            "content": (
                                "Summarize the following %s for reuse in future context. "
                                "Treat it as a research progress report for the next continuation round. "
                                "Retain the mathematical and scientific details needed to continue the research correctly. "
                                "Keep the final output under roughly %s tokens.\n\n%s"
                            )
                            % (purpose, token_budget, source),
                        }
                    ],
                    tool_schemas=[],
                )
                summary = (response.content or "").strip()
                if summary and "Moonshine processed the request in offline mode." not in summary:
                    return self._trim_text_to_budget(summary, token_budget)
            except Exception:
                pass

        bullets: List[str] = []
        for raw_line in source.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("## ") or line.startswith("# "):
                bullets.append(line.lstrip("# ").strip())
            elif ":" in line and len(line) <= 160:
                bullets.append(line)
            elif len(line) > 32:
                bullets.append(shorten(line, 160))
            if len(bullets) >= 8:
                break
        rendered = "\n".join("- %s" % item for item in bullets if item)
        return self._trim_text_to_budget(rendered or shorten(source, token_budget * 4), token_budget)

    def _summarize_with_provider(self, *, purpose: str, text: str, token_budget: int) -> str:
        """Compress text through bounded provider calls and concatenate chunk summaries."""
        chunks = split_text_by_token_budget(
            text,
            SUMMARY_PROVIDER_INPUT_TOKEN_BUDGET,
            model_name=self._token_model_name(),
        )
        if not chunks:
            return ""
        if len(chunks) == 1:
            return self._summarize_bounded_text_with_provider(
                purpose=purpose,
                text=chunks[0],
                token_budget=token_budget,
            )
        summaries = []
        for index, chunk in enumerate(chunks, start=1):
            summary = self._summarize_bounded_text_with_provider(
                purpose="%s chunk %s/%s" % (purpose, index, len(chunks)),
                text=chunk,
                token_budget=token_budget,
            )
            if summary:
                summaries.append(summary)
        return "\n\n".join(summaries)

    def _load_memory_index_excerpt(self) -> str:
        """Load the configured slice of MEMORY.md."""
        line_limit = max(1, int(self.config.context.memory_index_lines))
        lines = read_text(self.paths.memory_index_file).splitlines()[:line_limit]
        return self._trim_text_to_budget("\n".join(lines), self.config.context.memory_index_token_budget)

    def _render_stable_memory_file(self, alias: str, *, project_slug: str = "", token_budget: int = 240) -> str:
        """Render one durable memory file as compact bullets for default prompt use."""
        path = self.memory_manager.dynamic_store.resolve_path(alias, project_slug or None)
        entries = self.memory_manager.dynamic_store.parse_entries(path)
        if not entries:
            return ""
        lines = []
        for entry in entries[:5]:
            summary = str(entry.summary or entry.body or "").strip()
            if summary:
                lines.append("- %s: %s" % (entry.title, shorten(summary, 160)))
            else:
                lines.append("- %s" % entry.title)
        return self._trim_text_to_budget("\n".join(lines), token_budget)

    def _is_placeholder_project_context(self, text: str) -> bool:
        """Return whether the project context file is still a boilerplate placeholder."""
        stripped = (text or "").strip()
        if not stripped:
            return True
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        if len(lines) <= 2 and any("Moonshine maintains this file" in line for line in lines):
            return True
        return False

    def _is_placeholder_project_rules(self, text: str) -> bool:
        """Return whether project rules still contain only the default placeholder content."""
        stripped = (text or "").strip()
        if not stripped:
            return True
        placeholder_markers = [
            "Record the current target here when it becomes stable.",
            "Record assumptions, conventions, exclusions, and fixed notation here.",
            "Record the current branches, subgoals, or near-term priorities here.",
            "This file was created automatically by Moonshine.",
            "Add project-specific constraints here.",
            "Add the current research goal here.",
        ]
        return any(marker in stripped for marker in placeholder_markers)

    def build_startup_context(self, *, mode: str, project_slug: str, session_id: str) -> ContextBundle:
        """Load the startup context following the lightweight-index scheme."""
        static_rules = ""
        core_config = self._trim_text_to_budget(
            read_text(self.paths.core_config_file),
            self.config.context.config_token_budget,
        )
        user_profile = self._render_stable_memory_file(
            "user-profile",
            token_budget=max(120, int(self.config.context.project_context_token_budget)),
        )
        user_preferences = self._render_stable_memory_file(
            "user-preferences",
            token_budget=max(120, int(self.config.context.project_rules_token_budget)),
        )
        project_context_summary = ""
        project_rules_raw = self.memory_manager.static_store.load_project_rules(project_slug)
        project_rules = ""
        if not self._is_placeholder_project_rules(project_rules_raw):
            project_rules = self._trim_text_to_budget(
                project_rules_raw,
                self.config.context.project_rules_token_budget,
            )
        bundle = ContextBundle(
            static_rules=static_rules,
            core_config=core_config,
            user_profile=user_profile,
            user_preferences=user_preferences,
            memory_index="",
            project_context_summary=project_context_summary,
            project_rules=project_rules,
        )
        bundle.token_estimate = self.estimate_tokens(bundle.to_prompt_text())
        return bundle

    def _format_history_for_summary(self, messages: Sequence[Dict[str, object]]) -> str:
        """Format provider messages for summarization."""
        return "\n".join(self._render_message_line(item) for item in messages)

    def _context_threshold(self) -> int:
        """Return the compression threshold in tokens."""
        max_tokens = resolve_model_context_window(
            self._token_model_name(),
            configured=getattr(self.provider, "max_context_tokens", None)
            or getattr(self.config.provider, "max_context_tokens", 0),
        )
        configured = int(getattr(self.config.context, "compression_threshold_tokens", 0) or 0)
        if configured > 0:
            return max(256, min(configured, max_tokens))
        return max(256, int(float(self.config.context.warning_ratio) * max_tokens))

    def _tail_token_budget(self, aggressive: bool = False) -> int:
        """Return the protected tail budget in tokens."""
        base_budget = max(96, int(self._context_threshold() * float(self.config.context.tail_token_budget_ratio)))
        if aggressive:
            return max(64, int(base_budget * 0.6))
        return base_budget

    def _pressure_warning_tier(self, estimated_tokens: int, threshold_tokens: int) -> float:
        """Return the warning tier reached by the current context pressure."""
        if threshold_tokens <= 0:
            return 0.0
        progress = float(estimated_tokens) / float(threshold_tokens)
        if progress >= float(self.config.context.pressure_critical_ratio):
            return float(self.config.context.pressure_critical_ratio)
        if progress >= float(self.config.context.pressure_warning_ratio):
            return float(self.config.context.pressure_warning_ratio)
        return 0.0

    def context_pressure_snapshot(
        self,
        *,
        messages: Sequence[Dict[str, object]],
        system_prompt: str,
        tool_schemas: Optional[Sequence[Dict[str, object]]] = None,
    ) -> Dict[str, float]:
        """Return a small snapshot of current context pressure."""
        estimated = self.estimate_request_tokens(
            system_prompt=system_prompt,
            messages=messages,
            tool_schemas=tool_schemas,
        )
        threshold = self._context_threshold()
        progress = (float(estimated) / float(threshold)) if threshold else 0.0
        return {
            "estimated_tokens": float(estimated),
            "threshold_tokens": float(threshold),
            "progress": progress,
            "warning_tier": self._pressure_warning_tier(estimated, threshold),
        }

    def _session_raw_record_locations(self, session_id: str) -> Dict[str, str]:
        """Return runtime-relative raw record locations for one session."""
        if not session_id:
            return {}
        try:
            return {
                "messages": self.paths.session_messages_file(session_id).relative_to(self.paths.home).as_posix(),
                "transcript": self.paths.session_transcript_file(session_id).relative_to(self.paths.home).as_posix(),
                "tool_events": self.paths.session_tool_events_file(session_id).relative_to(self.paths.home).as_posix(),
                "provider_rounds_index": self.paths.session_provider_rounds_file(session_id).relative_to(self.paths.home).as_posix(),
                "provider_round_archives": self.paths.session_provider_round_archives_dir(session_id).relative_to(self.paths.home).as_posix(),
                "context_summaries": self.paths.session_context_summaries_file(session_id).relative_to(self.paths.home).as_posix(),
            }
        except ValueError:
            return {}

    def _history_summary_message(
        self,
        summary: str,
        chunk_index: int = 0,
        chunk_count: int = 0,
        session_id: str = "",
    ) -> Dict[str, str]:
        """Build a synthetic assistant summary message."""
        open_tag = "<session-history-summary>"
        if chunk_count > 1:
            open_tag = '<session-history-summary chunk="%s/%s">' % (chunk_index, chunk_count)
        locations = self._session_raw_record_locations(session_id)
        location_lines = []
        for label, path in locations.items():
            location_lines.append("- %s: `%s`" % (label, path))
        recovery_note = (
            "Note: the content below is compressed conversation history. "
            "If exact original wording, full tool payloads, or full provider-round records are needed, call `query_session_records` first; "
            "then use `read_runtime_file` for plain runtime files when useful. "
            "Raw record locations:\n%s" % ("\n".join(location_lines) if location_lines else "- unavailable for this session")
        )
        return {
            "role": "assistant",
            "content": "%s\n%s\n\n%s\n</session-history-summary>" % (open_tag, recovery_note, summary),
        }

    def _chunk_history_by_count(
        self,
        older_messages: Sequence[Dict[str, object]],
        *,
        chunk_count: int,
    ) -> List[List[Dict[str, object]]]:
        """Split older history into evenly sized message-count chunks."""
        source = [dict(item) for item in older_messages]
        if not source:
            return []
        target_chunk_count = max(1, int(chunk_count or 1))
        per_chunk_message_count = max(1, int(math.ceil(float(len(source)) / float(target_chunk_count))))
        chunks: List[List[Dict[str, object]]] = []
        for start in range(0, len(source), per_chunk_message_count):
            chunks.append(source[start : start + per_chunk_message_count])
        return chunks

    def _summarize_history_chunks(self, older_messages: Sequence[Dict[str, object]]) -> List[str]:
        """Summarize older history in chunks so early rounds retain more structure."""
        if not older_messages:
            return []
        per_chunk_budget = max(160, int(getattr(self.config.context, "history_compression_chunk_token_budget", 1500) or 1500))
        chunk_count = max(1, int(getattr(self.config.context, "history_compression_chunk_count", 60) or 60))
        chunks = self._chunk_history_by_count(older_messages, chunk_count=chunk_count)
        summaries = []
        for chunk_index, chunk in enumerate(chunks, start=1):
            summary = self._summarize_with_provider(
                purpose="conversation history chunk %s/%s" % (chunk_index, len(chunks)),
                text=self._format_history_for_summary(chunk),
                token_budget=per_chunk_budget,
            )
            summaries.append(summary)
        return summaries

    def _latest_reusable_context_summary(self, session_id: str, messages: Sequence[Dict[str, object]]) -> Optional[Dict[str, object]]:
        """Return the latest compression summary that still covers a prefix of the current history."""
        if not session_id:
            return None
        current_ids = [int(item.get("id")) for item in messages if item.get("id") is not None]
        if not current_ids:
            return None
        current_id_set = set(current_ids)
        for item in reversed(read_jsonl(self.paths.session_context_summaries_file(session_id))):
            if not isinstance(item, dict) or str(item.get("mode") or "") != "summary":
                continue
            covered_ids = []
            for value in list(item.get("covered_message_ids") or []):
                try:
                    covered_ids.append(int(value))
                except (TypeError, ValueError):
                    continue
            if not covered_ids:
                continue
            try:
                head_count = max(0, int(item.get("head_messages", 0) or 0))
            except (TypeError, ValueError):
                head_count = 0
            if not set(covered_ids).issubset(current_id_set):
                continue
            if current_ids[head_count : head_count + len(covered_ids)] != covered_ids:
                continue
            return dict(item)
        return None

    def _messages_with_reused_summary(
        self,
        *,
        session_id: str,
        messages: Sequence[Dict[str, object]],
        summary_record: Dict[str, object],
    ) -> List[Dict[str, object]]:
        """Replace an already-compressed history prefix with its cached summary message."""
        covered_count = len(list(summary_record.get("covered_message_ids") or []))
        summary = str(summary_record.get("summary") or "").strip()
        if covered_count <= 0 or not summary:
            return list(messages)
        try:
            head_count = max(0, int(summary_record.get("head_messages", 0) or 0))
        except (TypeError, ValueError):
            head_count = 0
        source = list(messages)
        return [
            *[dict(item) for item in source[:head_count]],
            self._history_summary_message(summary, session_id=session_id),
            *[dict(item) for item in source[head_count + covered_count:]],
        ]

    def _provider_message(self, item: Dict[str, object]) -> Dict[str, object]:
        """Strip local-only metadata before sending a provider message."""
        allowed = {"role", "content", "reasoning_content", "tool_calls", "tool_call_id", "name"}
        return {key: value for key, value in dict(item).items() if key in allowed and value is not None}

    def _message_token_cost(self, item: Dict[str, object]) -> int:
        """Estimate one message cost including tool-call metadata."""
        return self.estimate_message_tokens([item])

    def _align_boundary_forward(self, messages: Sequence[Dict[str, object]], index: int) -> int:
        """Move a boundary forward so compression never starts on a tool result."""
        while index < len(messages) and str(messages[index].get("role", "")) == "tool":
            index += 1
        return index

    def _align_boundary_backward(self, messages: Sequence[Dict[str, object]], index: int) -> int:
        """Move a boundary backward so tool_call/result groups stay intact."""
        if index <= 0 or index >= len(messages):
            return index
        check = index - 1
        while check >= 0 and str(messages[check].get("role", "")) == "tool":
            check -= 1
        if check >= 0:
            candidate = dict(messages[check])
            if str(candidate.get("role", "")) == "assistant" and candidate.get("tool_calls"):
                index = check
        return index

    def _recover_leading_tool_results_as_context(self, messages: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
        """Convert orphaned leading tool results into plain context after tail trimming."""
        source = [dict(item) for item in messages]
        index = 0
        while index < len(source) and str(source[index].get("role", "")) == "tool":
            index += 1
        if index <= 0:
            return source
        preserved_results = []
        for item in source[:index]:
            name = str(item.get("name") or "")
            call_id = str(item.get("tool_call_id") or "")
            content = str(item.get("content") or "")
            preserved_results.append(
                "Tool: %s\nTool call id: %s\nFull tool result:\n%s"
                % (name or "(unknown)", call_id or "(unknown)", content)
            )
        recovered = {
            "role": "assistant",
            "content": (
                "Compressed context note: the following recent tool result(s) were preserved as plain context "
                "because their structured tool-call parent was outside the retained message window.\n"
                + "\n\n".join(preserved_results)
            ),
        }
        return [recovered] + source[index:]

    def _previous_suffix_group_start(self, messages: Sequence[Dict[str, object]], end: int, *, floor: int = 0) -> int:
        """Return the start index of the structured message group ending at end."""
        if end <= floor:
            return floor
        start = end - 1
        if str(messages[start].get("role", "")) != "tool":
            return start
        while start > floor and str(messages[start - 1].get("role", "")) == "tool":
            start -= 1
        parent_index = start - 1
        if parent_index >= floor:
            parent = dict(messages[parent_index])
            if str(parent.get("role", "")) == "assistant" and parent.get("tool_calls"):
                return parent_index
        return start

    def _trim_recent_suffix_by_one_group(self, messages: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
        """Drop the oldest retained recent group while keeping tool-call groups valid."""
        source = [dict(item) for item in messages]
        if not source:
            return []
        first_group_end = 1
        if str(source[0].get("role", "")) == "assistant" and source[0].get("tool_calls"):
            first_group_end = 1
            while first_group_end < len(source) and str(source[first_group_end].get("role", "")) == "tool":
                first_group_end += 1
        return self._recover_leading_tool_results_as_context(source[first_group_end:])

    def _find_tail_cut_by_tokens(self, messages: Sequence[Dict[str, object]], head_end: int, *, aggressive: bool = False) -> int:
        """Find a structurally valid recent suffix whose token cost fits the tail budget."""
        token_budget = self._tail_token_budget(aggressive=aggressive)
        if token_budget <= 0 or head_end >= len(messages):
            return len(messages)
        accumulated = 0
        cut_index = len(messages)
        end = len(messages)
        while end > head_end:
            group_start = self._previous_suffix_group_start(messages, end, floor=head_end)
            if group_start >= end:
                break
            group_tokens = self.estimate_message_tokens(messages[group_start:end])
            if accumulated + group_tokens > token_budget:
                break
            accumulated += group_tokens
            cut_index = group_start
            end = group_start
        return max(head_end, min(len(messages), cut_index))

    def _summarize_tool_result_payload(self, message: Dict[str, object]) -> str:
        """Create a cheap one-line summary for an old tool result."""
        content = str(message.get("content", "") or "")
        try:
            parsed = json.loads(content)
        except ValueError:
            parsed = {}
        if isinstance(parsed, dict) and parsed.get("name"):
            tool_name = str(parsed.get("name", "tool"))
            output = self._stringify_payload(parsed.get("output", {}))
            error = str(parsed.get("error", "") or "")
            suffix = "error=%s" % shorten(error, 80) if error else shorten(output, 160)
            return "[tool-result] %s -> %s" % (tool_name, suffix or "(no output)")
        return "[tool-result] %s" % shorten(content, 180)

    def _prune_old_tool_results(
        self,
        messages: Sequence[Dict[str, object]],
        *,
        protected_tail_start: int,
        protected_head_end: int,
    ) -> Tuple[List[Dict[str, object]], int]:
        """Cheap pre-pass: prune large old tool outputs and oversized old tool-call arguments."""
        result = json.loads(json.dumps(list(messages), ensure_ascii=False))
        pruned_count = 0
        tool_output_threshold = max(64, int(self.config.context.tool_output_prune_char_threshold))
        tool_arg_threshold = max(128, int(self.config.context.tool_call_argument_prune_char_threshold))

        for index in range(protected_head_end, max(protected_head_end, protected_tail_start)):
            item = dict(result[index])
            if str(item.get("role", "")) == "tool":
                content = str(item.get("content", "") or "")
                if len(content) > tool_output_threshold:
                    result[index]["content"] = self._summarize_tool_result_payload(item)
                    pruned_count += 1
            elif str(item.get("role", "")) == "assistant" and item.get("tool_calls"):
                modified = False
                for tool_call in result[index].get("tool_calls") or []:
                    function = tool_call.get("function") or {}
                    arguments = str(function.get("arguments", "") or "")
                    if len(arguments) > tool_arg_threshold:
                        function["arguments"] = arguments[:200].rstrip() + "...[truncated]"
                        tool_call["function"] = function
                        modified = True
                if modified:
                    pruned_count += 1
        return result, pruned_count

    def compact_provider_messages(
        self,
        *,
        messages: Sequence[Dict[str, object]],
        system_prompt: str,
        session_id: str,
        artifact_label: str = "provider-history",
        aggressive: bool = False,
        tool_schemas: Optional[Sequence[Dict[str, object]]] = None,
    ) -> Tuple[List[Dict[str, object]], Dict[str, object]]:
        """Compress older provider messages when the token budget is tight."""
        original_messages = list(messages)
        candidate = json.loads(json.dumps(original_messages, ensure_ascii=False))
        estimated = self.estimate_request_tokens(
            system_prompt=system_prompt,
            messages=candidate,
            tool_schemas=tool_schemas,
        )
        threshold = self._context_threshold()
        pressure_progress = (float(estimated) / float(threshold)) if threshold else 0.0
        metadata: Dict[str, object] = {
            "estimated_tokens": estimated,
            "threshold_tokens": threshold,
            "pressure_progress": pressure_progress,
            "warning_tier": self._pressure_warning_tier(estimated, threshold),
            "compressed_history": False,
            "kept_recent_messages": len(candidate),
            "summarized_messages": 0,
            "summary_chunk_count": 0,
            "pruned_tool_items": 0,
            "head_messages": 0,
            "tail_messages": len(candidate),
            "aggressive": bool(aggressive),
        }
        reused_summary = self._latest_reusable_context_summary(session_id, original_messages)
        if reused_summary is not None and not aggressive:
            reused_messages = self._messages_with_reused_summary(
                session_id=session_id,
                messages=original_messages,
                summary_record=reused_summary,
            )
            reused_estimate = self.estimate_request_tokens(
                system_prompt=system_prompt,
                messages=reused_messages,
                tool_schemas=tool_schemas,
            )
            if reused_estimate < threshold:
                provider_reused_messages = [self._provider_message(item) for item in reused_messages]
                metadata.update(
                    {
                        "estimated_tokens": reused_estimate,
                        "pressure_progress": (float(reused_estimate) / float(threshold)) if threshold else 0.0,
                        "warning_tier": self._pressure_warning_tier(reused_estimate, threshold),
                        "compressed_history": True,
                        "kept_recent_messages": max(0, len(original_messages) - len(list(reused_summary.get("covered_message_ids") or []))),
                        "summarized_messages": len(list(reused_summary.get("covered_message_ids") or [])),
                        "summary_chunk_count": max(1, len(list(reused_summary.get("summary_chunks") or [])) or 1),
                        "pruned_tool_items": 0,
                        "head_messages": 0,
                        "tail_messages": len(provider_reused_messages) - 1,
                        "reused_summary": True,
                    }
                )
                return provider_reused_messages, metadata

        if (estimated < threshold and not aggressive) or not candidate:
            return [self._provider_message(item) for item in candidate], metadata

        head_end = min(len(candidate), max(0, int(self.config.context.protect_first_message_count)))
        head_end = self._align_boundary_forward(candidate, head_end)
        tail_start = self._find_tail_cut_by_tokens(candidate, head_end, aggressive=aggressive)
        candidate, pruned_count = self._prune_old_tool_results(
            candidate,
            protected_tail_start=tail_start,
            protected_head_end=head_end,
        )
        estimated = self.estimate_request_tokens(
            system_prompt=system_prompt,
            messages=candidate,
            tool_schemas=tool_schemas,
        )
        pressure_progress = (float(estimated) / float(threshold)) if threshold else 0.0
        if estimated < threshold and pruned_count and not aggressive:
            metadata.update(
                {
                    "estimated_tokens": estimated,
                    "pressure_progress": pressure_progress,
                    "warning_tier": self._pressure_warning_tier(estimated, threshold),
                    "compressed_history": True,
                    "pruned_tool_items": pruned_count,
                    "head_messages": head_end,
                    "tail_messages": len(candidate) - tail_start,
                    "kept_recent_messages": len(candidate) - tail_start,
                    "summarized_messages": 0,
                    "summary_chunk_count": 0,
                }
            )
            append_jsonl(
                self.paths.session_context_summaries_file(session_id),
                {
                    "created_at": utc_now(),
                    "label": artifact_label,
                    "mode": "tool-prune",
                    "older_message_count": max(0, tail_start - head_end),
                    "kept_recent_messages": len(candidate) - tail_start,
                    "summary": "",
                    "summary_chunks": [],
                    "raw_record_locations": self._session_raw_record_locations(session_id),
                    "recovery_tool": "query_session_records",
                    "pruned_tool_items": pruned_count,
                    "aggressive": bool(aggressive),
                },
            )
            return [self._provider_message(item) for item in candidate], metadata

        head_messages = list(candidate[:head_end])
        older_messages = list(candidate[head_end:tail_start])
        raw_recent = list(candidate[tail_start:])

        if older_messages:
            salvage_result = self.memory_manager.extract_pre_compress(
                session_id=session_id,
                project_slug=self.session_store.get_session_meta(session_id).get("project_slug", ""),
                window_text=self._format_history_for_summary(older_messages),
            )
            if salvage_result.get("entries") or salvage_result.get("conclusions"):
                self.session_store.append_turn_event(
                    session_id,
                    {
                        "type": "pre_compress_memory_extracted",
                        "text": "Persisted durable facts before compressing older context.",
                        "created_at": utc_now(),
                        "entries": salvage_result.get("entries", 0),
                        "conclusions": salvage_result.get("conclusions", 0),
                        "label": artifact_label,
                    },
                )

        if not older_messages:
            compressed = list(candidate)
            while (
                self.estimate_request_tokens(
                    system_prompt=system_prompt,
                    messages=compressed,
                    tool_schemas=tool_schemas,
            )
                > threshold
                and raw_recent
            ):
                raw_recent = self._trim_recent_suffix_by_one_group(raw_recent)
                compressed = head_messages + raw_recent
            compacted_estimate = self.estimate_request_tokens(
                system_prompt=system_prompt,
                messages=compressed,
                tool_schemas=tool_schemas,
            )
            metadata.update(
                {
                    "estimated_tokens": compacted_estimate,
                    "pressure_progress": (float(compacted_estimate) / float(threshold)) if threshold else 0.0,
                    "warning_tier": self._pressure_warning_tier(
                        compacted_estimate,
                        threshold,
                    ),
                    "compressed_history": len(compressed) != len(candidate),
                    "kept_recent_messages": len(raw_recent),
                    "pruned_tool_items": pruned_count,
                    "head_messages": len(head_messages),
                    "tail_messages": len(raw_recent),
                }
            )
            return [self._provider_message(item) for item in compressed], metadata

        chunk_summaries = self._summarize_history_chunks(older_messages)
        summary_messages = [
            self._history_summary_message(
                summary,
                chunk_index=index + 1,
                chunk_count=len(chunk_summaries),
                session_id=session_id,
            )
            for index, summary in enumerate(chunk_summaries)
        ]
        compressed = head_messages + summary_messages + raw_recent

        if (
            len(summary_messages) > 1
            and self.estimate_request_tokens(
                system_prompt=system_prompt,
                messages=compressed,
                tool_schemas=tool_schemas,
            )
            > threshold
        ):
            merged_summary = self._summarize_with_provider(
                purpose="older conversation history",
                text="\n\n".join(chunk_summaries),
                token_budget=self.config.context.retrieval_summary_token_budget,
            )
            chunk_summaries = [merged_summary]
            summary_messages = [self._history_summary_message(merged_summary, session_id=session_id)]
            compressed = head_messages + summary_messages + raw_recent

        while (
            self.estimate_request_tokens(
                system_prompt=system_prompt,
                messages=compressed,
                tool_schemas=tool_schemas,
            )
            > threshold
            and raw_recent
        ):
            raw_recent = self._trim_recent_suffix_by_one_group(raw_recent)
            compressed = head_messages + summary_messages + raw_recent

        if summary_messages and self.estimate_request_tokens(
            system_prompt=system_prompt,
            messages=compressed,
            tool_schemas=tool_schemas,
        ) > threshold:
            available_for_summary = max(
                96,
                threshold
                - self.estimate_tokens(system_prompt)
                - self.estimate_message_tokens(head_messages)
                - self.estimate_message_tokens(raw_recent)
                - self.estimate_tool_schema_tokens(tool_schemas or [])
                - 12,
            )
            compact_summary = self._summarize_with_provider(
                purpose="compressed older conversation history",
                text="\n\n".join(chunk_summaries),
                token_budget=available_for_summary,
            )
            chunk_summaries = [compact_summary]
            summary_messages = [self._history_summary_message(compact_summary, session_id=session_id)]
            compressed = head_messages + summary_messages + raw_recent

        while (
            self.estimate_request_tokens(
                system_prompt=system_prompt,
                messages=compressed,
                tool_schemas=tool_schemas,
            )
            > threshold
            and raw_recent
        ):
            raw_recent = self._trim_recent_suffix_by_one_group(raw_recent)
            compressed = head_messages + summary_messages + raw_recent

        final_estimate = self.estimate_request_tokens(
            system_prompt=system_prompt,
            messages=compressed,
            tool_schemas=tool_schemas,
        )
        append_jsonl(
            self.paths.session_context_summaries_file(session_id),
            {
                "created_at": utc_now(),
                "label": artifact_label,
                "mode": "summary",
                "older_message_count": len(older_messages),
                "kept_recent_messages": len(raw_recent),
                "summary": "\n\n".join(chunk_summaries),
                "summary_chunks": list(chunk_summaries),
                "raw_record_locations": self._session_raw_record_locations(session_id),
                "recovery_tool": "query_session_records",
                "source_chunk_count": len(chunk_summaries),
                "target_chunk_count": max(1, int(getattr(self.config.context, "history_compression_chunk_count", 60) or 60)),
                "covered_message_ids": [
                    int(item.get("id"))
                    for item in original_messages[head_end:tail_start]
                    if item.get("id") is not None
                ],
                "pruned_tool_items": pruned_count,
                "head_messages": len(head_messages),
                "tail_messages": len(raw_recent),
                "aggressive": bool(aggressive),
            },
        )
        metadata.update(
            {
                "estimated_tokens": final_estimate,
                "pressure_progress": (float(final_estimate) / float(threshold)) if threshold else 0.0,
                "warning_tier": self._pressure_warning_tier(final_estimate, threshold),
                "compressed_history": True,
                "kept_recent_messages": len(raw_recent),
                "summarized_messages": len(older_messages),
                "summary_chunk_count": len(chunk_summaries),
                "pruned_tool_items": pruned_count,
                "head_messages": len(head_messages),
                "tail_messages": len(raw_recent),
            }
        )
        return [self._provider_message(item) for item in compressed], metadata

    def build_provider_messages(
        self,
        *,
        session_id: str,
        user_message: str,
        system_prompt: str,
        tool_schemas: Optional[Sequence[Dict[str, object]]] = None,
    ) -> Tuple[List[Dict[str, object]], Dict[str, object]]:
        """Build provider messages and summarize older history when needed."""
        history = self.session_store.get_all_messages(session_id)
        history_messages = []
        for item in history:
            message = {"id": item.get("id"), "role": item["role"], "content": str(item["content"])}
            metadata = dict(item.get("metadata") or {})
            reasoning_content = str(metadata.get("reasoning_content") or "")
            if str(item.get("role") or "") == "assistant" and reasoning_content:
                message["reasoning_content"] = reasoning_content
            history_messages.append(message)
        candidate = list(history_messages) + [{"role": "user", "content": user_message}]
        compressed, metadata = self.compact_provider_messages(
            messages=candidate,
            system_prompt=system_prompt,
            session_id=session_id,
            artifact_label="turn-start",
            tool_schemas=tool_schemas,
        )
        if not metadata.get("compressed_history"):
            metadata["kept_recent_messages"] = len(history_messages)
        return compressed, metadata

    def _time_distance_seconds(self, left: str, right: str) -> Optional[float]:
        """Return the absolute distance between two timestamps in seconds."""
        left_dt = parse_utc_timestamp(left)
        right_dt = parse_utc_timestamp(right)
        if left_dt is None or right_dt is None:
            return None
        return abs((left_dt - right_dt).total_seconds())

    def _score_related_blob(self, *, query: str, anchor_text: str, blob: str, anchor_created_at: str, candidate_created_at: str) -> float:
        """Rank a candidate artifact relative to one anchor hit."""
        score = overlap_score(query, blob)
        if anchor_text:
            score += 0.5 * overlap_score(anchor_text, blob)
            if anchor_text.lower() in blob.lower():
                score += 0.25
        distance = self._time_distance_seconds(anchor_created_at, candidate_created_at)
        if distance is not None:
            score += max(0.0, 0.35 - min(distance / 900.0, 0.35))
        return score

    def _render_tool_event(self, item: Dict[str, object]) -> str:
        """Render one tool event for local-window recovery."""
        parts = [
            "Tool: %s" % item.get("tool", ""),
            "Arguments: %s" % self._stringify_payload(item.get("arguments", {})),
            "Output: %s" % self._stringify_payload(item.get("output", {})),
        ]
        if item.get("error"):
            parts.append("Error: %s" % item.get("error"))
        return "\n".join(parts)

    def _render_provider_round(self, item: Dict[str, object]) -> str:
        """Render one provider round as compact trace text."""
        lines = [
            "Provider Round: %s" % item.get("title", ""),
            "Phase: %s" % item.get("phase", "main"),
        ]
        if item.get("tool_schema_names"):
            lines.append("Tool Schemas: %s" % ", ".join(item.get("tool_schema_names") or []))
        for message in list(item.get("messages") or []):
            lines.append(self._render_message_line(dict(message)))
        response = dict(item.get("response") or {})
        if response.get("tool_calls"):
            for tool_call in response.get("tool_calls") or []:
                lines.append(
                    "Response Tool Call: %s(%s)"
                    % (
                        tool_call.get("name", ""),
                        shorten(self._stringify_payload(tool_call.get("arguments", {})), 160),
                    )
                )
        if response.get("content") or response.get("reasoning_content"):
            response_like = {"role": "assistant"}
            if response.get("reasoning_content"):
                response_like["reasoning_content"] = response.get("reasoning_content", "")
            response_like["content"] = response.get("content", "")
            lines.append("Response Message: %s" % json.dumps(response_like, ensure_ascii=False))
        return "\n".join(line for line in lines if line)

    def _render_conversation_event(self, item: Dict[str, object]) -> str:
        """Render one structured conversation event."""
        label = "%s/%s" % (item.get("event_kind", "event"), item.get("role", "unknown"))
        rendered = "[%s] %s" % (label, item.get("content", ""))
        payload = dict(item.get("payload") or {})
        metadata = dict(payload.get("metadata") or {})
        message_payload = dict(payload.get("message") or {})
        reasoning_content = str(metadata.get("reasoning_content") or message_payload.get("reasoning_content") or "").strip()
        if reasoning_content:
            rendered = json.dumps(
                {
                    "event_kind": item.get("event_kind", "event"),
                    "role": item.get("role", "unknown"),
                    "reasoning_content": reasoning_content,
                    "content": item.get("content", ""),
                },
                ensure_ascii=False,
            )
        return rendered

    def _find_source_message_id(self, *, session_id: str, source_excerpt: str, source_role: str = "") -> Optional[int]:
        """Best-effort recovery of the source message anchor for a memory item."""
        if not source_excerpt.strip():
            return None
        ranked = []
        for item in self.session_store.get_all_messages(session_id):
            if source_role and str(item.get("role")) != source_role:
                continue
            score = overlap_score(source_excerpt, str(item.get("content", "")))
            if source_excerpt.lower() in str(item.get("content", "")).lower():
                score += 0.5
            if score > 0:
                ranked.append((score, int(item["id"])))
        ranked.sort(reverse=True)
        if not ranked:
            return None
        return ranked[0][1]

    def _select_related_tool_events(
        self,
        *,
        session_id: str,
        query: str,
        anchor_text: str,
        anchor_created_at: str,
        limit: int = 2,
    ) -> List[str]:
        """Select the most relevant tool events for one session hit."""
        ranked = []
        for item in self.session_store.get_tool_events(session_id):
            rendered = self._render_tool_event(item)
            score = self._score_related_blob(
                query=query,
                anchor_text=anchor_text,
                blob=rendered,
                anchor_created_at=anchor_created_at,
                candidate_created_at=str(item.get("created_at", "")),
            )
            if score > 0:
                ranked.append((score, rendered))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [pair[1] for pair in ranked[:limit]]

    def _select_related_provider_rounds(
        self,
        *,
        session_id: str,
        query: str,
        anchor_text: str,
        anchor_created_at: str,
        limit: int = 1,
    ) -> List[str]:
        """Select the most relevant provider rounds for one session hit."""
        ranked = []
        for item in self.session_store.get_provider_rounds(session_id):
            rendered = self._render_provider_round(item)
            score = self._score_related_blob(
                query=query,
                anchor_text=anchor_text,
                blob=rendered,
                anchor_created_at=anchor_created_at,
                candidate_created_at=str(item.get("created_at", "")),
            )
            if score > 0:
                ranked.append((score, rendered))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [pair[1] for pair in ranked[:limit]]

    def _build_session_context_window(self, item: Dict[str, object], query: str) -> str:
        """Reconstruct a local complete session window around one anchor hit."""
        metadata = dict(item.get("metadata") or {})
        session_id = str(metadata.get("session_id", ""))
        anchor_message_id = metadata.get("message_id")
        anchor_event_id = metadata.get("event_id")
        anchor_text = str(item.get("text", ""))
        anchor_created_at = str(metadata.get("created_at", ""))
        lines = [
            "## Session Window",
            "Session: %s" % session_id,
        ]
        if metadata.get("project_slug"):
            lines.append("Project: %s" % metadata.get("project_slug"))
        lines.append("Anchor: %s" % item.get("title", "session-hit"))

        conversation_window = []
        if session_id and anchor_message_id is not None:
            conversation_window = self.session_store.get_conversation_window(
                session_id,
                anchor_message_id=int(anchor_message_id),
                anchor_event_id=int(anchor_event_id) if anchor_event_id is not None else None,
                before=4,
                after=6,
            )
        elif session_id and anchor_event_id is not None:
            conversation_window = self.session_store.get_conversation_window(
                session_id,
                anchor_event_id=int(anchor_event_id),
                before=4,
                after=6,
            )
        if conversation_window:
            lines.append("### Local Event Sequence")
            for event in conversation_window:
                lines.append(self._render_conversation_event(event))
        elif session_id and anchor_message_id is not None:
            raw_window = self.session_store.get_message_window(
                session_id,
                int(anchor_message_id),
                before=2,
                after=2,
            )
            if raw_window:
                lines.append("### Nearby Messages")
                for message in raw_window:
                    metadata = dict(message.get("metadata") or {})
                    provider_like = {
                        "role": message["role"],
                    }
                    reasoning_content = str(metadata.get("reasoning_content") or "").strip()
                    if reasoning_content:
                        provider_like["reasoning_content"] = reasoning_content
                    provider_like["content"] = message["content"]
                    lines.append(json.dumps(provider_like, ensure_ascii=False))

        if session_id:
            tool_windows = self._select_related_tool_events(
                session_id=session_id,
                query=query,
                anchor_text=anchor_text,
                anchor_created_at=anchor_created_at,
                limit=2,
            )
            if tool_windows:
                lines.append("### Related Tool Results")
                lines.extend(tool_windows)

            provider_windows = self._select_related_provider_rounds(
                session_id=session_id,
                query=query,
                anchor_text=anchor_text,
                anchor_created_at=anchor_created_at,
                limit=1,
            )
            if provider_windows:
                lines.append("### Related Provider Round")
                lines.extend(provider_windows)
        return "\n".join(line for line in lines if line)

    def _build_dynamic_context_window(self, item: Dict[str, object], query: str) -> str:
        """Reconstruct a local context window around one dynamic-memory hit."""
        metadata = dict(item.get("metadata") or {})
        lines = [
            "## Dynamic Memory Window",
            "Title: %s" % item.get("title", ""),
            "Source: %s" % metadata.get("source", ""),
        ]
        if metadata.get("project_slug"):
            lines.append("Project: %s" % metadata.get("project_slug"))
        if metadata.get("relative_path"):
            lines.append("Path: %s" % metadata.get("relative_path"))
        if metadata.get("summary"):
            lines.append("Summary: %s" % metadata.get("summary"))
        if metadata.get("tags"):
            lines.append("Tags: %s" % ", ".join(metadata.get("tags") or []))
        lines.append("Body:")
        lines.append(str(item.get("text", "")))

        source_session_id = str(metadata.get("source_session_id", ""))
        if source_session_id:
            anchor_message_id = self._find_source_message_id(
                session_id=source_session_id,
                source_excerpt=str(metadata.get("source_excerpt", "")),
                source_role=str(metadata.get("source_message_role", "")),
            )
            if anchor_message_id is not None:
                lines.append(
                    self._build_session_context_window(
                        {
                            "title": "Source Session for %s" % item.get("title", ""),
                            "text": str(metadata.get("source_excerpt", "")) or str(item.get("text", "")),
                            "metadata": {
                                "session_id": source_session_id,
                                "project_slug": metadata.get("project_slug", ""),
                                "created_at": metadata.get("updated_at", ""),
                                "message_id": anchor_message_id,
                            },
                        },
                        query,
                    )
                )
        return "\n".join(line for line in lines if line)

    def _build_knowledge_context_window(self, item: Dict[str, object], query: str) -> str:
        """Reconstruct a local context window around one structured-knowledge hit."""
        metadata = dict(item.get("metadata") or {})
        lines = [
            "## Knowledge Window",
            "Title: %s" % item.get("title", ""),
            "Status: %s" % metadata.get("status", ""),
        ]
        if metadata.get("project_slug"):
            lines.append("Project: %s" % metadata.get("project_slug"))
        if metadata.get("tags"):
            lines.append("Tags: %s" % ", ".join(metadata.get("tags") or []))
        if metadata.get("source_type"):
            lines.append("Source Type: %s" % metadata.get("source_type"))
        if metadata.get("source_ref"):
            lines.append("Source Ref: %s" % metadata.get("source_ref"))
        lines.append("Statement:")
        lines.append(str(metadata.get("statement", "")))
        if metadata.get("proof_sketch"):
            lines.append("Proof Sketch:")
            lines.append(str(metadata.get("proof_sketch", "")))

        source_ref = str(metadata.get("source_ref", ""))
        if source_ref.startswith("session-"):
            anchor_message_id = self._find_source_message_id(
                session_id=source_ref,
                source_excerpt=str(metadata.get("statement", "")) or str(item.get("text", "")),
            )
            if anchor_message_id is not None:
                lines.append(
                    self._build_session_context_window(
                        {
                            "title": "Source Session for %s" % item.get("title", ""),
                            "text": str(metadata.get("statement", "")) or str(item.get("text", "")),
                            "metadata": {
                                "session_id": source_ref,
                                "project_slug": metadata.get("project_slug", ""),
                                "created_at": metadata.get("updated_at", ""),
                                "message_id": anchor_message_id,
                            },
                        },
                        query,
                    )
                )
        return "\n".join(line for line in lines if line)

    def _build_research_context_window(self, item: Dict[str, object], query: str) -> str:
        """Reconstruct a local context window around one research-log hit."""
        metadata = dict(item.get("metadata") or {})
        retrieval_mode = str(metadata.get("retrieval_mode") or "")
        exact_excerpt = str(metadata.get("exact_excerpt") or item.get("text") or "").strip()
        raw_text = str(metadata.get("raw_text") or "").strip()
        lines = [
            "## Research Log Window",
            "Title: %s" % item.get("title", ""),
            "Type: %s" % metadata.get("artifact_type", ""),
            "Stage: %s" % metadata.get("stage", ""),
            "Focus Activity: %s" % metadata.get("focus_activity", ""),
            "Status: %s" % metadata.get("status", ""),
            "Review Status: %s" % metadata.get("review_status", ""),
        ]
        if metadata.get("project_slug"):
            lines.append("Project: %s" % metadata.get("project_slug"))
        if metadata.get("tags"):
            lines.append("Tags: %s" % ", ".join(metadata.get("tags") or []))
        if metadata.get("next_action"):
            lines.append("Recorded Next Action: %s" % metadata.get("next_action"))
        if metadata.get("content_path"):
            lines.append("Source Path: %s" % metadata.get("content_path"))
        if metadata.get("source_type"):
            lines.append("Indexed Source: %s" % metadata.get("source_type"))
        if metadata.get("claim_hash"):
            lines.append("Claim Hash: %s" % metadata.get("claim_hash"))
        lines.append("Summary:")
        lines.append(str(metadata.get("summary", "")) or str(item.get("text", "")))
        if exact_excerpt:
            lines.append("Exact Slice:")
            lines.append(exact_excerpt)

        full_content = ""
        content_path = str(metadata.get("content_path", "") or "")
        if content_path:
            full_content = read_text(self.paths.home / content_path)
        if raw_text:
            lines.append("Raw Content:")
            lines.append(raw_text)
        elif full_content.strip() and (
            retrieval_mode != "research_index"
            or self.estimate_tokens(full_content) <= 1400
        ):
            lines.append("Raw Content:")
            lines.append(full_content.strip())
        elif full_content.strip():
            lines.append("Raw Content:")
            lines.append("(full source is retained on disk; use the exact slice above and the artifact path for targeted follow-up)")
        elif metadata.get("content_inline") and retrieval_mode != "research_index":
            lines.append("Stored Content:")
            lines.append(str(metadata.get("content_inline", "")))

        session_id = str(metadata.get("session_id", "") or "")
        if session_id:
            anchor_message_id = self._find_source_message_id(
                session_id=session_id,
                source_excerpt=str(metadata.get("summary", "")) or str(metadata.get("content_inline", "")),
            )
            if anchor_message_id is not None:
                lines.append(
                    self._build_session_context_window(
                        {
                            "title": "Source Session for %s" % item.get("title", ""),
                            "text": str(metadata.get("summary", "")) or str(item.get("text", "")),
                            "metadata": {
                                "session_id": session_id,
                                "project_slug": metadata.get("project_slug", ""),
                                "created_at": metadata.get("created_at", ""),
                                "message_id": anchor_message_id,
                            },
                        },
                        query,
                    )
                )
        return "\n".join(line for line in lines if line)

    def _normalize_session_hits(self, rows: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
        """Normalize session search hits."""
        normalized = []
        for index, item in enumerate(rows):
            project_label = ("[%s] " % item["project_slug"]) if item.get("project_slug") else ""
            metadata = dict(item.get("metadata") or {})
            reasoning_content = str(metadata.get("reasoning_content") or "").strip()
            text = str(item["content"])
            if reasoning_content:
                text = json.dumps(
                    {
                        "role": item["role"],
                        "reasoning_content": reasoning_content,
                        "content": str(item["content"]),
                    },
                    ensure_ascii=False,
                )
            normalized.append(
                {
                    "key": "session:%s" % item["id"],
                    "source": "session",
                    "rank": index + 1,
                    "title": "%s[%s] %s" % (project_label, item["role"], shorten(item["content"], 72)),
                    "text": text,
                    "metadata": {
                        "message_id": item["id"],
                        "role": item["role"],
                        "session_id": item["session_id"],
                        "created_at": item["created_at"],
                        "project_slug": item.get("project_slug", ""),
                        "reasoning_content": reasoning_content,
                    },
                }
            )
        return normalized

    def _normalize_event_hits(self, rows: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
        """Normalize structured conversation-event hits."""
        normalized = []
        for index, item in enumerate(rows):
            project_label = ("[%s] " % item["project_slug"]) if item.get("project_slug") else ""
            event_label = "%s/%s" % (item.get("event_kind", "event"), item.get("role", "unknown"))
            payload = dict(item.get("payload") or {})
            metadata = dict(payload.get("metadata") or {})
            message_payload = dict(payload.get("message") or {})
            reasoning_content = str(metadata.get("reasoning_content") or message_payload.get("reasoning_content") or "").strip()
            text = str(item["content"])
            if reasoning_content:
                text = json.dumps(
                    {
                        "event_kind": item.get("event_kind", "event"),
                        "role": item.get("role", "unknown"),
                        "reasoning_content": reasoning_content,
                        "content": str(item["content"]),
                    },
                    ensure_ascii=False,
                )
            normalized.append(
                {
                    "key": "session-event:%s" % item["id"],
                    "source": "session-event",
                    "rank": index + 1,
                    "title": "%s[%s] %s" % (project_label, event_label, shorten(item["content"], 72)),
                    "text": text,
                    "metadata": {
                        "event_id": item["id"],
                        "message_id": item.get("message_id"),
                        "event_kind": item.get("event_kind", ""),
                        "role": item.get("role", ""),
                        "session_id": item["session_id"],
                        "created_at": item["created_at"],
                        "project_slug": item.get("project_slug", ""),
                        "reasoning_content": reasoning_content,
                    },
                }
            )
        return normalized

    def _normalize_dynamic_hits(self, rows: Sequence[object]) -> List[Dict[str, object]]:
        """Normalize dynamic-memory search hits."""
        normalized = []
        for index, item in enumerate(rows):
            project_label = ("[%s] " % getattr(item, "project_slug", "")) if getattr(item, "project_slug", "") else ""
            normalized.append(
                {
                    "key": "dynamic:%s" % getattr(item, "slug", index),
                    "source": "dynamic",
                    "rank": index + 1,
                    "title": "%s%s" % (project_label, getattr(item, "title", getattr(item, "slug", "dynamic-memory"))),
                    "text": getattr(item, "body", "") or getattr(item, "summary", ""),
                    "metadata": {
                        "relative_path": getattr(item, "relative_path", ""),
                        "project_slug": getattr(item, "project_slug", ""),
                        "source": getattr(item, "source", ""),
                        "summary": getattr(item, "summary", ""),
                        "tags": list(getattr(item, "tags", []) or []),
                        "updated_at": getattr(item, "updated_at", ""),
                        "source_session_id": getattr(item, "source_session_id", ""),
                        "source_message_role": getattr(item, "source_message_role", ""),
                        "source_excerpt": getattr(item, "source_excerpt", ""),
                    },
                }
            )
        return normalized

    def _serialize_dynamic_hit_rows(self, rows: Sequence[object]) -> List[Dict[str, object]]:
        """Serialize dynamic-memory entries for tool output."""
        serialized = []
        for item in rows:
            serialized.append(
                {
                    "slug": getattr(item, "slug", ""),
                    "title": getattr(item, "title", ""),
                    "summary": getattr(item, "summary", ""),
                    "body": getattr(item, "body", ""),
                    "source": getattr(item, "source", ""),
                    "project_slug": getattr(item, "project_slug", ""),
                    "relative_path": getattr(item, "relative_path", ""),
                    "tags": list(getattr(item, "tags", []) or []),
                    "source_session_id": getattr(item, "source_session_id", ""),
                    "source_message_role": getattr(item, "source_message_role", ""),
                    "source_excerpt": getattr(item, "source_excerpt", ""),
                    "updated_at": getattr(item, "updated_at", ""),
                }
            )
        return serialized

    def _normalize_knowledge_hits(self, rows: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
        """Normalize knowledge search hits."""
        normalized = []
        for index, item in enumerate(rows):
            project_label = ("[%s] " % item["project_slug"]) if item.get("project_slug") else ""
            normalized.append(
                {
                    "key": "knowledge:%s" % item["id"],
                    "source": "knowledge",
                    "rank": index + 1,
                    "title": "%s%s" % (project_label, item["title"]),
                    "text": "%s\n%s" % (item["statement"], item.get("proof_sketch", "")),
                    "metadata": {
                        "id": item["id"],
                        "statement": item["statement"],
                        "proof_sketch": item.get("proof_sketch", ""),
                        "status": item["status"],
                        "project_slug": item["project_slug"],
                        "path": item.get("path", ""),
                        "tags": list(item.get("tags", []) or []),
                        "source_type": item.get("source_type", ""),
                        "source_ref": item.get("source_ref", ""),
                        "updated_at": item.get("updated_at", ""),
                    },
                }
            )
        return normalized

    def _normalize_research_hits(self, rows: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
        """Normalize persisted research-log hits."""
        normalized = []
        for index, item in enumerate(rows):
            project_label = ("[%s] " % item["project_slug"]) if item.get("project_slug") else ""
            normalized.append(
                {
                    "key": "research-log:%s" % item.get("id", index),
                    "source": "research-log",
                    "rank": index + 1,
                    "title": "%s%s: %s"
                    % (
                        project_label,
                        item.get("artifact_type", "artifact"),
                        item.get("title", "") or shorten(item.get("summary", ""), 72),
                    ),
                    "text": item.get("summary", "") or item.get("content_inline", ""),
                    "metadata": {
                        "artifact_id": item.get("id", ""),
                        "artifact_type": item.get("artifact_type", ""),
                        "channel": item.get("channel", "") or dict(item.get("metadata") or {}).get("channel", ""),
                        "retrieval_mode": item.get("retrieval_mode", "") or dict(item.get("metadata") or {}).get("retrieval_mode", ""),
                        "source_type": item.get("source_type", "") or dict(item.get("metadata") or {}).get("source_type", ""),
                        "exact_excerpt": item.get("exact_excerpt", "") or dict(item.get("metadata") or {}).get("exact_excerpt", ""),
                        "raw_text": dict(item.get("metadata") or {}).get("raw_text", ""),
                        "claim_hash": item.get("claim_hash", "") or dict(item.get("metadata") or {}).get("claim_hash", ""),
                        "branch_id": item.get("branch_id", "") or dict(item.get("metadata") or {}).get("branch_id", ""),
                        "project_slug": item.get("project_slug", ""),
                        "stage": item.get("stage", ""),
                        "focus_activity": item.get("focus_activity", ""),
                        "status": item.get("status", ""),
                        "review_status": item.get("review_status", ""),
                        "summary": item.get("summary", ""),
                        "content_inline": item.get("content_inline", ""),
                        "content_path": item.get("content_path", ""),
                        "session_id": item.get("session_id", ""),
                        "created_at": item.get("created_at", ""),
                        "tags": list(item.get("tags", []) or []),
                        "next_action": item.get("next_action", ""),
                        "related_ids": list(item.get("related_ids", []) or []),
                        "metadata": dict(item.get("metadata") or {}),
                    },
                }
            )
        return normalized

    def _rrf_merge(self, ranked_lists: Sequence[Sequence[Dict[str, object]]], k: int = 60) -> List[Dict[str, object]]:
        """Merge ranked lists with reciprocal rank fusion."""
        merged: Dict[str, Dict[str, object]] = {}
        for ranked in ranked_lists:
            for item in ranked:
                key = str(item["key"])
                entry = merged.setdefault(
                    key,
                    {
                        "key": key,
                        "source": item["source"],
                        "title": item["title"],
                        "text": item["text"],
                        "metadata": dict(item.get("metadata") or {}),
                        "rrf_score": 0.0,
                    },
                )
                entry["rrf_score"] += 1.0 / float(k + int(item["rank"]))
        return sorted(merged.values(), key=lambda value: value["rrf_score"], reverse=True)

    def query_memory(
        self,
        *,
        query: str,
        project_slug: Optional[str],
        session_id: str,
        all_projects: bool = False,
        types: Optional[Sequence[str]] = None,
        channels: Optional[Sequence[str]] = None,
        channel_mode: str = "search",
        limit_per_channel: int = 3,
        prefer_raw: bool = False,
    ) -> Dict[str, object]:
        """Run on-demand retrieval across memory layers.

        Project research memory is sourced only from research_log.jsonl and its
        rebuildable SQLite index. `types` is the preferred research-log scope;
        `channels` is accepted as a legacy alias. If no project research-log hit
        is found, fall back to non-project memory layers.
        """
        research_log_types: List[str] = []
        for type_name in list(types or []) + list(channels or []):
            normalized_type = normalize_research_log_type(str(type_name))
            if normalized_type and normalized_type not in research_log_types:
                research_log_types.append(normalized_type)
        research_log_hits = self.research_log.search(
            query=query,
            project_slug=project_slug,
            types=research_log_types,
            limit=max(1, int(limit_per_channel or 3) * max(1, len(research_log_types) or 1)),
        )
        if research_log_hits:
            summary_lines = [
                "[{record_type}] {title}\n{content}".format(
                    record_type=str(item.get("type") or item.get("artifact_type") or "research_note"),
                    title=str(item.get("title") or ""),
                    content=str(item.get("content_inline") or item.get("content") or ""),
                )
                for item in research_log_hits[:6]
            ]
            return {
                "query": query,
                "project_scope": project_slug or "all-projects",
                "all_projects": bool(all_projects),
                "types": research_log_types,
                "channels": research_log_types,
                "channel_mode": str(channel_mode or "search"),
                "limit_per_channel": int(limit_per_channel or 3),
                "prefer_raw": True,
                "summary": "\n\n".join(summary_lines),
                "compressed_windows": [
                    {
                        "key": str(item.get("key") or item.get("id") or ""),
                        "source": "research-log",
                        "title": str(item.get("title") or ""),
                        "rrf_score": float(item.get("score") or 0.0),
                        "summary": str(item.get("content_inline") or item.get("content") or ""),
                        "window_excerpt": str(item.get("content_inline") or item.get("content") or ""),
                        "metadata": dict(item.get("metadata") or {}),
                    }
                    for item in research_log_hits[:4]
                ],
                "sources": research_log_hits[:6],
                "research_log_hits": research_log_hits,
                "research_hits": research_log_hits,
                "dynamic_hits": [],
                "session_hits": [],
                "event_hits": [],
                "knowledge_hits": [],
                "graph_hits": [],
            }
        if research_log_types:
            return {
                "query": query,
                "project_scope": project_slug or "all-projects",
                "all_projects": bool(all_projects),
                "types": research_log_types,
                "channels": research_log_types,
                "channel_mode": str(channel_mode or "search"),
                "limit_per_channel": int(limit_per_channel or 3),
                "prefer_raw": True,
                "summary": "",
                "compressed_windows": [],
                "sources": [],
                "research_log_hits": [],
                "research_hits": [],
                "dynamic_hits": [],
                "session_hits": [],
                "event_hits": [],
                "knowledge_hits": [],
                "graph_hits": [],
            }
        raw_hits = self.memory_manager.query_memory_sources(
            query,
            project_slug,
            session_id,
            research_channels=[],
            research_channel_mode=channel_mode,
            limit_per_channel=limit_per_channel,
        )
        dynamic_hits = list(raw_hits.get("dynamic_hits") or [])
        session_hits = list(raw_hits.get("session_hits") or [])
        event_hits = list(raw_hits.get("event_hits") or [])
        knowledge_hits = list(raw_hits.get("knowledge_hits") or [])
        research_hits: List[Dict[str, object]] = []
        graph_hits: List[Dict[str, object]] = list(raw_hits.get("graph_hits") or [])
        ranked_lists = [
            self._normalize_dynamic_hits(dynamic_hits),
            self._normalize_session_hits(session_hits),
            self._normalize_event_hits(event_hits),
            self._normalize_knowledge_hits(knowledge_hits),
        ]
        merged = self._rrf_merge(ranked_lists)

        window_limit = min(4, len(merged))
        per_window_budget = max(
            180,
            min(
                320,
                int(self.config.context.retrieval_summary_token_budget / max(1, window_limit)),
            ),
        )
        compressed_windows = []
        for item in merged[:window_limit]:
            if item["source"] == "session":
                window_text = self._build_session_context_window(item, query)
            elif item["source"] == "session-event":
                window_text = self._build_session_context_window(item, query)
            elif item["source"] == "dynamic":
                window_text = self._build_dynamic_context_window(item, query)
            elif item["source"] == "knowledge":
                window_text = self._build_knowledge_context_window(item, query)
            else:
                window_text = "[%s] %s\n%s" % (item["source"], item["title"], item["text"])
            if prefer_raw:
                summary = self._trim_text_to_budget(window_text, per_window_budget)
            else:
                summary = self._summarize_with_provider(
                    purpose="%s local context window" % item["source"],
                    text=window_text,
                    token_budget=per_window_budget,
                )
            compressed_windows.append(
                {
                    "key": item["key"],
                    "source": item["source"],
                    "title": item["title"],
                    "rrf_score": item["rrf_score"],
                    "summary": summary,
                    "window_excerpt": self._trim_text_to_budget(window_text, max(per_window_budget * 2, 240)),
                    "metadata": dict(item.get("metadata") or {}),
                }
            )

        summary_inputs = []
        for window in compressed_windows:
            summary_inputs.append(
                "[%s] %s\n%s" % (window["source"], window["title"], window["summary"])
            )
        if not summary_inputs:
            for item in merged[:6]:
                summary_inputs.append("[%s] %s: %s" % (item["source"], item["title"], shorten(item["text"], 180)))
        if not summary_inputs:
            summary_inputs.append("[graph] Knowledge graph layer is disabled in this build.")
        elif not graph_hits:
            summary_inputs.append("[graph] Knowledge graph layer is disabled in this build.")

        if prefer_raw:
            summary = self._trim_text_to_budget(
                "\n\n".join(summary_inputs),
                self.config.context.retrieval_summary_token_budget,
            )
        else:
            summary = self._summarize_with_provider(
                purpose="retrieved memory windows",
                text="\n\n".join(summary_inputs),
                token_budget=self.config.context.retrieval_summary_token_budget,
            )
        return {
            "query": query,
            "project_scope": project_slug or "all-projects",
            "all_projects": bool(all_projects),
            "types": research_log_types,
            "channels": research_log_types,
            "channel_mode": str(channel_mode or "search"),
            "limit_per_channel": int(limit_per_channel or 3),
            "prefer_raw": bool(prefer_raw),
            "summary": summary,
            "compressed_windows": compressed_windows,
            "sources": merged[:6],
            "dynamic_hits": self._serialize_dynamic_hit_rows(dynamic_hits),
            "session_hits": session_hits,
            "event_hits": event_hits,
            "research_hits": research_hits,
            "knowledge_hits": knowledge_hits,
            "graph_hits": graph_hits,
        }
