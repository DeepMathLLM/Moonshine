"""Core Moonshine conversation loop and direct terminal runner."""

from __future__ import annotations

import argparse
import difflib
import json
import traceback
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from moonshine.agent_runtime.prompt_builder import build_system_prompt
from moonshine.agent_runtime.research_workflow import ResearchWorkflowManager
from moonshine.json_schema import JsonSchemaValidationError, validate_json_schema
from moonshine.model_tools import collect_tool_schemas, handle_function_calls
from moonshine.providers import ProviderResponse, ProviderToolCall
from moonshine.utils import shorten, utc_now


@dataclass
class AgentEvent:
    """Incremental agent event for terminal and gateway consumers."""

    type: str
    text: str = ""
    payload: Dict[str, object] = field(default_factory=dict)


@dataclass
class ConversationBudget:
    """Hard limits for one user turn."""

    max_model_rounds: int
    max_tool_rounds: int
    max_empty_response_retries: int
    max_tool_validation_retries: int
    max_consecutive_errors: int
    max_tool_calls_per_round: int


@dataclass
class PreparedToolCall:
    """Normalized tool-call record."""

    call_id: str
    original_name: str
    name: str
    arguments: Dict[str, object]
    status: str
    error: str = ""
    repaired_from: str = ""


@dataclass
class ConversationState:
    """Mutable per-turn runtime state."""

    user_message: str
    mode: str
    project_slug: str
    session_id: str
    agent_slug: str
    system_prompt: str
    provider_messages: List[Dict[str, object]]
    tool_schemas: List[Dict[str, object]]
    runtime: Dict[str, object]
    budget: ConversationBudget
    valid_tool_names: Sequence[str]
    model_round: int = 0
    tool_rounds: int = 0
    empty_response_retries: int = 0
    tool_validation_retries: int = 0
    consecutive_errors: int = 0
    post_tool_nudge_used: bool = False
    summary_pass_used: bool = False
    fallback_response_text: str = ""
    fallback_response_streamed: bool = False
    final_text: str = ""
    final_reason: str = ""
    turn_transcript: List[Dict[str, object]] = field(default_factory=list)
    highest_context_warning_tier: float = 0.0
    overflow_recovery_attempts: int = 0
    research_workflow_snapshot: Dict[str, object] = field(default_factory=dict)


class AIAgent(object):
    """Moonshine conversation runner."""

    def __init__(self, *, config, paths, provider, verification_provider, memory_manager, session_store, agent_manager, skill_manager, tool_manager, context_manager):
        self.config = config
        self.paths = paths
        self.provider = provider
        self.verification_provider = verification_provider
        self.memory_manager = memory_manager
        self.session_store = session_store
        self.agent_manager = agent_manager
        self.skill_manager = skill_manager
        self.skill_store = skill_manager.store
        self.tool_manager = tool_manager
        self.tool_registry = tool_manager
        self.context_manager = context_manager
        self.research_workflow = ResearchWorkflowManager(
            paths=paths,
            provider=provider,
            memory_manager=memory_manager,
            session_store=session_store,
            config=config,
        )

    def _default_agent_slug_for_mode(self, mode: str) -> str:
        """Return the implicit active agent for a given mode."""
        return "research-control-loop" if mode == "research" else self.agent_manager.default_slug

    def _build_runtime(self, *, mode: str, project_slug: str, session_id: str, agent_slug: str = "") -> Dict[str, object]:
        """Build the tool runtime mapping."""
        resolved_agent_slug = str(agent_slug or self._default_agent_slug_for_mode(mode)).strip()
        exposure = getattr(self.config, "exposure", None)
        return {
            "paths": self.paths,
            "config": self.config,
            "memory_manager": self.memory_manager,
            "session_store": self.session_store,
            "agent_manager": self.agent_manager,
            "skill_manager": self.skill_manager,
            "skill_store": self.skill_store,
            "tool_manager": self.tool_manager,
            "context_manager": self.context_manager,
            "research_workflow": self.research_workflow,
            "provider": self.provider,
            "verification_provider": self.verification_provider,
            "verification_provider_inherit_from_main": bool(
                getattr(self.config.verification_provider, "inherit_from_main", True)
            ),
            "mode": mode,
            "project_slug": project_slug,
            "session_id": session_id,
            "agent_slug": resolved_agent_slug,
            "exposure": {
                "tools_include": list(getattr(exposure, "tools_include", []) or []),
                "tools_exclude": list(getattr(exposure, "tools_exclude", []) or []),
                "skills_include": list(getattr(exposure, "skills_include", []) or []),
                "skills_exclude": list(getattr(exposure, "skills_exclude", []) or []),
            },
        }

    def _build_budget(self) -> ConversationBudget:
        """Resolve loop budgets from config."""
        agent_config = self.config.agent
        return ConversationBudget(
            max_model_rounds=max(1, int(getattr(agent_config, "max_model_rounds", 12))),
            max_tool_rounds=max(1, int(getattr(agent_config, "max_tool_rounds", 8))),
            max_empty_response_retries=max(0, int(getattr(agent_config, "max_empty_response_retries", 2))),
            max_tool_validation_retries=max(0, int(getattr(agent_config, "max_tool_validation_retries", 2))),
            max_consecutive_errors=max(1, int(getattr(agent_config, "max_consecutive_errors", 3))),
            max_tool_calls_per_round=max(1, int(getattr(agent_config, "max_tool_calls_per_round", 6))),
        )

    def _record_turn_event(self, session_id: str, event_type: str, text: str = "", **payload: object) -> None:
        """Persist loop decisions for debugging and traceability."""
        self.session_store.append_turn_event(
            session_id,
            {
                "type": event_type,
                "text": text,
                "created_at": utc_now(),
                **dict(payload),
            },
        )

    def _snapshot_messages(self, messages: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
        """Create a JSON-safe snapshot of provider messages before mutation."""
        return json.loads(json.dumps(list(messages), ensure_ascii=False))

    def _normalized_response_payload(self, response: ProviderResponse) -> Dict[str, object]:
        """Render a normalized provider response for trace persistence."""
        return {
            "content": response.content,
            "tool_calls": [
                {
                    "name": item.name,
                    "arguments": dict(item.arguments or {}),
                    "call_id": item.call_id,
                }
                for item in response.tool_calls
            ],
        }

    def _record_provider_round(
        self,
        *,
        state: ConversationState,
        phase: str,
        title: str,
        system_prompt: str,
        request_messages: Sequence[Dict[str, object]],
        response: ProviderResponse,
        tool_schemas: Sequence[Dict[str, object]],
    ) -> None:
        """Persist one provider request/response round for human inspection."""
        self.session_store.append_provider_round(
            state.session_id,
            {
                "created_at": utc_now(),
                "phase": phase,
                "title": title,
                "model_round": state.model_round,
                "system_prompt": system_prompt,
                "messages": self._snapshot_messages(request_messages),
                "tool_schema_names": [str(item.get("name", "")) for item in list(tool_schemas or []) if str(item.get("name", ""))],
                "response": self._normalized_response_payload(response),
            },
        )

    def _append_turn_transcript(self, state: ConversationState, event: Dict[str, object]) -> None:
        """Append one JSON-safe event to the current-turn archival transcript."""
        if state.mode != "research":
            return
        state.turn_transcript.append(json.loads(json.dumps(dict(event), ensure_ascii=False)))

    def _emit_status(self, state: ConversationState, text: str, **payload: object) -> Optional[AgentEvent]:
        """Create and persist a status event when enabled."""
        data = dict(payload)
        data.setdefault("model_round", state.model_round)
        data.setdefault("tool_rounds", state.tool_rounds)
        self._record_turn_event(
            state.session_id,
            "status",
            text,
            **data,
        )
        if not self.config.agent.emit_status_events:
            return None
        return AgentEvent(type="status", text=text, payload=data)

    def _emit_context_pressure_if_needed(self, state: ConversationState, snapshot: Dict[str, float]) -> Optional[AgentEvent]:
        """Emit one of the configured context-pressure warnings."""
        warning_tier = float(snapshot.get("warning_tier", 0.0) or 0.0)
        if warning_tier <= state.highest_context_warning_tier:
            return None
        state.highest_context_warning_tier = warning_tier
        progress_percent = int(round(float(snapshot.get("progress", 0.0)) * 100.0))
        threshold_tokens = int(snapshot.get("threshold_tokens", 0.0) or 0.0)
        estimated_tokens = int(snapshot.get("estimated_tokens", 0.0) or 0.0)
        tier_percent = int(round(warning_tier * 100.0))
        return self._emit_status(
            state,
            "Context pressure warning: %s%% of the compaction threshold reached (%s/%s tokens, tier %s%%)."
            % (progress_percent, estimated_tokens, threshold_tokens, tier_percent),
            estimated_tokens=estimated_tokens,
            threshold_tokens=threshold_tokens,
            progress=snapshot.get("progress", 0.0),
            warning_tier=warning_tier,
        )

    def _maybe_reset_context_warning(self, state: ConversationState, snapshot: Dict[str, float]) -> None:
        """Clear the warning tier once compaction brings pressure back down."""
        if float(snapshot.get("warning_tier", 0.0) or 0.0) < float(self.config.context.pressure_warning_ratio):
            state.highest_context_warning_tier = 0.0

    def _is_context_overflow_error(self, exc: Exception) -> bool:
        """Return True when an exception looks like a context-length failure."""
        text = str(exc).lower()
        status_code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
        if status_code in (400, 413):
            return True
        phrases = (
            "context length",
            "context length exceeded",
            "context size",
            "maximum context",
            "context window",
            "too many tokens",
            "token limit",
            "prompt is too long",
            "request entity too large",
            "payload too large",
            "reduce the length",
            "max tokens too large",
        )
        return any(phrase in text for phrase in phrases)

    def _recover_from_context_overflow(self, state: ConversationState, *, phase: str, error_text: str) -> bool:
        """Apply aggressive compaction and retry when a provider overflows."""
        limit = max(0, int(self.config.context.overflow_retry_limit))
        if state.overflow_recovery_attempts >= limit:
            return False
        compacted_messages, compression_meta = self.context_manager.compact_provider_messages(
            messages=state.provider_messages,
            system_prompt=state.system_prompt,
            session_id=state.session_id,
            artifact_label="overflow-recovery",
            aggressive=True,
            tool_schemas=state.tool_schemas,
        )
        changed = json.dumps(compacted_messages, ensure_ascii=False) != json.dumps(state.provider_messages, ensure_ascii=False)
        if not changed:
            return False
        state.provider_messages = compacted_messages
        state.overflow_recovery_attempts += 1
        self._record_turn_event(
            state.session_id,
            "context_overflow_recovery",
            "Recovered from a context overflow by aggressively compacting history.",
            phase=phase,
            error=error_text,
            recovery_attempt=state.overflow_recovery_attempts,
            estimated_tokens=compression_meta.get("estimated_tokens", 0),
            summarized_messages=compression_meta.get("summarized_messages", 0),
            kept_recent_messages=compression_meta.get("kept_recent_messages", 0),
            pruned_tool_items=compression_meta.get("pruned_tool_items", 0),
        )
        self._maybe_reset_context_warning(
            state,
            {
                "warning_tier": compression_meta.get("warning_tier", 0.0),
            },
        )
        return True

    def _normalize_text(self, value: object) -> str:
        """Normalize provider content into plain text."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            if "text" in value:
                return self._normalize_text(value.get("text"))
            if "content" in value:
                return self._normalize_text(value.get("content"))
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, list):
            parts = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif isinstance(item, dict) and "text" in item:
                    parts.append(str(item["text"]))
                else:
                    parts.append(self._normalize_text(item))
            return "\n".join(part for part in parts if part)
        return str(value)

    def _normalize_response(self, response: Optional[ProviderResponse]) -> ProviderResponse:
        """Normalize a provider response before loop handling."""
        response = response or ProviderResponse()
        normalized_calls = []
        for index, tool_call in enumerate(response.tool_calls or []):
            arguments = tool_call.arguments
            if arguments is None:
                arguments = {}
            elif not isinstance(arguments, dict):
                arguments = {"value": arguments}
            normalized_calls.append(
                ProviderToolCall(
                    name=self._normalize_text(tool_call.name).strip(),
                    arguments=dict(arguments),
                    call_id=tool_call.call_id or "tool-call-%s" % (index + 1),
                )
            )
        return ProviderResponse(
            content=self._normalize_text(response.content),
            tool_calls=normalized_calls,
            raw_payload=dict(response.raw_payload or {}),
        )

    def _repair_tool_name(self, name: str, valid_tool_names: Sequence[str]) -> tuple[str, str]:
        """Repair a tool name with exact-insensitive and fuzzy matching."""
        normalized_name = (name or "").strip()
        if normalized_name in valid_tool_names:
            return normalized_name, ""

        lower_map = {item.lower(): item for item in valid_tool_names}
        if normalized_name.lower() in lower_map:
            return lower_map[normalized_name.lower()], normalized_name

        matches = difflib.get_close_matches(normalized_name, list(valid_tool_names), n=1, cutoff=0.72)
        if matches:
            return matches[0], normalized_name
        return normalized_name, ""

    def _normalize_tool_arguments(self, arguments: object) -> tuple[Dict[str, object], str]:
        """Normalize tool arguments and detect malformed payloads."""
        if arguments is None:
            return {}, ""
        if isinstance(arguments, dict):
            if "_raw_arguments" in arguments and len(arguments) == 1:
                return {}, "Tool arguments were malformed JSON and could not be parsed."
            return dict(arguments), ""
        if isinstance(arguments, str):
            cleaned = arguments.strip()
            if not cleaned:
                return {}, ""
            try:
                parsed = json.loads(cleaned)
            except ValueError:
                return {}, "Tool arguments were malformed JSON and could not be parsed."
            if isinstance(parsed, dict):
                return parsed, ""
            return {"value": parsed}, ""
        if isinstance(arguments, list):
            return {"value": list(arguments)}, ""
        return {"value": arguments}, ""

    def _prepare_tool_calls(
        self,
        state: ConversationState,
        tool_calls: Sequence[ProviderToolCall],
    ) -> tuple[List[PreparedToolCall], bool]:
        """Validate, normalize, and guardrail tool calls."""
        prepared: List[PreparedToolCall] = []
        seen_signatures = set()
        executable_count = 0
        invalid_batch = False
        available_tools = ", ".join(sorted(state.valid_tool_names))

        for index, call in enumerate(tool_calls):
            repaired_name, repaired_from = self._repair_tool_name(call.name, state.valid_tool_names)
            arguments, argument_error = self._normalize_tool_arguments(call.arguments)
            call_id = call.call_id or "tool-call-%s" % (index + 1)
            status = "execute"
            error = ""

            if repaired_name not in state.valid_tool_names:
                status = "invalid"
                error = "Unknown tool '%s'. Available tools: %s." % (call.name, available_tools)
                invalid_batch = True
            elif argument_error:
                status = "invalid"
                error = argument_error
                invalid_batch = True
            else:
                tool_definition = self.tool_manager.get_tool(repaired_name)
                if tool_definition is not None:
                    try:
                        validate_json_schema(arguments, dict(tool_definition.parameters or {}))
                    except JsonSchemaValidationError as exc:
                        status = "invalid"
                        error = "Arguments for tool '%s' do not satisfy its JSON schema: %s." % (repaired_name, exc)
                        invalid_batch = True
                if status == "invalid":
                    prepared.append(
                        PreparedToolCall(
                            call_id=call_id,
                            original_name=call.name,
                            name=repaired_name,
                            arguments=arguments,
                            status=status,
                            error=error,
                            repaired_from=repaired_from,
                        )
                    )
                    continue
                signature = "%s|%s" % (
                    repaired_name,
                    json.dumps(arguments, sort_keys=True, ensure_ascii=False),
                )
                if signature in seen_signatures:
                    status = "duplicate"
                    error = "Skipped duplicate tool call in the same model response."
                elif executable_count >= state.budget.max_tool_calls_per_round:
                    status = "capped"
                    error = "Skipped because the per-round tool limit (%s) was reached." % state.budget.max_tool_calls_per_round
                else:
                    seen_signatures.add(signature)
                    executable_count += 1

            prepared.append(
                PreparedToolCall(
                    call_id=call_id,
                    original_name=call.name,
                    name=repaired_name,
                    arguments=arguments,
                    status=status,
                    error=error,
                    repaired_from=repaired_from,
                )
            )

        if invalid_batch:
            for item in prepared:
                if item.status != "invalid":
                    item.status = "skipped"
                    item.error = "Skipped because another tool call in the same model response was invalid. Retry the tool batch."
        return prepared, invalid_batch

    def _build_assistant_tool_message(
        self,
        content: str,
        prepared_calls: Sequence[PreparedToolCall],
    ) -> Dict[str, object]:
        """Build the assistant tool-call message for the next model round."""
        return {
            "role": "assistant",
            "content": content or "",
            "tool_calls": [
                {
                    "id": item.call_id,
                    "type": "function",
                    "function": {
                        "name": item.name or item.original_name,
                        "arguments": json.dumps(item.arguments, ensure_ascii=False),
                    },
                }
                for item in prepared_calls
            ],
        }

    def _assistant_tool_event_content(self, content: str, prepared_calls: Sequence[PreparedToolCall]) -> str:
        """Render an assistant tool-call turn as compact text."""
        lines = []
        if content.strip():
            lines.append("Assistant: %s" % content.strip())
        for item in prepared_calls:
            lines.append(
                "Tool Call: %s(%s)"
                % (
                    item.name or item.original_name,
                    shorten(json.dumps(item.arguments, ensure_ascii=False), 200),
                )
            )
        return "\n".join(lines)
    def _build_synthetic_tool_result(self, item: PreparedToolCall) -> Dict[str, object]:
        """Build a synthetic tool result for invalid or skipped calls."""
        output = {
            "status": item.status,
            "message": item.error,
        }
        if item.repaired_from:
            output["repaired_from"] = item.repaired_from
        return {
            "name": item.name or item.original_name,
            "call_id": item.call_id,
            "arguments": dict(item.arguments),
            "output": output,
            "error": item.error or None,
        }

    def _visible_tool_output(self, result: Dict[str, object]) -> object:
        """Return the compact tool-result payload that should remain visible to the main model."""
        name = str(result.get("name") or "").strip()
        output = dict(result.get("output") or {})
        structured_research_recorders = {
            "record_solve_attempt",
            "record_failed_path",
        }
        if name in structured_research_recorders:
            compact = {
                "status": "recorded" if not result.get("error") else "error",
                "id": str(output.get("id") or ""),
                "artifact_type": str(output.get("artifact_type") or ""),
                "channel": str(output.get("channel") or ""),
                "path": str(output.get("content_path") or output.get("artifact_path") or ""),
            }
            return {key: value for key, value in compact.items() if value}
        if name in {"store_conclusion", "add_knowledge"}:
            compact = {
                "status": str(output.get("status") or ""),
                "stored_as": str(output.get("stored_as") or ""),
                "id": str(output.get("id") or output.get("artifact_id") or ""),
                "path": str(output.get("path") or output.get("artifact_path") or ""),
                "reason": str(output.get("reason") or ""),
            }
            return {key: value for key, value in compact.items() if value}
        return output

    def record_tool_result_message(self, provider_messages: List[Dict[str, object]], result: Dict[str, object]) -> None:
        """Append a tool result message to the provider transcript."""
        provider_messages.append(
            {
                "role": "tool",
                "tool_call_id": result["call_id"],
                "content": json.dumps(
                    {
                        "name": result["name"],
                        "output": self._visible_tool_output(result),
                        "error": result.get("error"),
                    },
                    ensure_ascii=False,
                ),
            }
        )

    def _record_tool_results(
        self,
        state: ConversationState,
        results: Sequence[Dict[str, object]],
    ):
        """Persist tool results, append them to the provider transcript, and emit events."""
        for result in results:
            event_payload = {
                "tool": result["name"],
                "call_id": result["call_id"],
                "arguments": result["arguments"],
                "output": result["output"],
                "error": result.get("error"),
                "tool_round": state.tool_rounds,
                "created_at": utc_now(),
            }
            self.session_store.append_tool_event(state.session_id, event_payload)
            self._append_turn_transcript(
                state,
                {
                    "kind": "tool_result",
                    "tool": result["name"],
                    "call_id": result["call_id"],
                    "arguments": result["arguments"],
                    "output": result["output"],
                    "error": result.get("error"),
                    "tool_round": state.tool_rounds,
                    "created_at": event_payload["created_at"],
                },
            )
            self.record_tool_result_message(state.provider_messages, result)
            self._record_turn_event(
                state.session_id,
                "tool_result" if not result.get("error") else "tool_error",
                result["name"],
                tool_round=state.tool_rounds,
                call_id=result["call_id"],
                error=result.get("error"),
            )
            if state.mode == "research":
                try:
                    self.research_workflow.observe_tool_result(
                        project_slug=state.project_slug,
                        session_id=state.session_id,
                        tool_name=str(result["name"]),
                        arguments=dict(result.get("arguments") or {}),
                        output=dict(result.get("output") or {}),
                        error=str(result.get("error") or ""),
                    )
                except Exception as exc:
                    self._record_turn_event(
                        state.session_id,
                        "research_tool_observer_error",
                        str(exc),
                        tool=result.get("name", ""),
                    )
            yield AgentEvent(
                type="tool_error" if result.get("error") else "tool_result",
                text=result["name"],
                payload=result,
            )

    def _recent_messages_end_with_tool_result(self, provider_messages: Sequence[Dict[str, object]]) -> bool:
        """Return True when the previous model round ended with tool results."""
        if not provider_messages:
            return False
        for item in reversed(provider_messages):
            role = item.get("role")
            if role == "tool":
                return True
            if role in {"assistant", "user"}:
                return False
        return False

    def _stream_provider_round(self, state: ConversationState):
        """Run one provider round and stream text deltas as agent events."""
        streamed_chunks: List[str] = []
        response = None
        pressure_snapshot = self.context_manager.context_pressure_snapshot(
            messages=state.provider_messages,
            system_prompt=state.system_prompt,
            tool_schemas=state.tool_schemas,
        )
        warning_event = self._emit_context_pressure_if_needed(state, pressure_snapshot)
        if warning_event is not None:
            yield warning_event
        compacted_messages, compression_meta = self.context_manager.compact_provider_messages(
            messages=state.provider_messages,
            system_prompt=state.system_prompt,
            session_id=state.session_id,
            artifact_label="live-provider",
            tool_schemas=state.tool_schemas,
        )
        state.provider_messages = compacted_messages
        if compression_meta.get("compressed_history"):
            self._maybe_reset_context_warning(
                state,
                {
                    "warning_tier": compression_meta.get("warning_tier", 0.0),
                },
            )
            self._record_turn_event(
                state.session_id,
                "live_context_compressed",
                "Compressed older in-turn context before a provider round.",
                estimated_tokens=compression_meta.get("estimated_tokens", 0),
                summarized_messages=compression_meta.get("summarized_messages", 0),
                kept_recent_messages=compression_meta.get("kept_recent_messages", 0),
                summary_chunk_count=compression_meta.get("summary_chunk_count", 0),
                pruned_tool_items=compression_meta.get("pruned_tool_items", 0),
                pressure_progress=compression_meta.get("pressure_progress", 0.0),
            )
            status_event = self._emit_status(
                state,
                "Compressed older in-turn context to stay within the token budget.",
                estimated_tokens=compression_meta.get("estimated_tokens", 0),
                summarized_messages=compression_meta.get("summarized_messages", 0),
                kept_recent_messages=compression_meta.get("kept_recent_messages", 0),
                pruned_tool_items=compression_meta.get("pruned_tool_items", 0),
            )
            if status_event is not None:
                yield status_event
        request_messages = self._snapshot_messages(state.provider_messages)
        for provider_event in self.provider.stream_generate(
            system_prompt=state.system_prompt,
            messages=state.provider_messages,
            tool_schemas=state.tool_schemas,
        ):
            if provider_event.type == "text_delta" and provider_event.text:
                streamed_chunks.append(provider_event.text)
                yield AgentEvent(
                    type="text_delta",
                    text=provider_event.text,
                    payload={"model_round": state.model_round},
                )
            elif provider_event.type == "response":
                response = provider_event.response or ProviderResponse()

        combined_text = "".join(streamed_chunks)
        normalized_response = self._normalize_response(response)
        if combined_text and not normalized_response.content.strip():
            normalized_response.content = combined_text
        self._record_provider_round(
            state=state,
            phase="main",
            title="Provider Round %s" % state.model_round,
            system_prompt=state.system_prompt,
            request_messages=request_messages,
            response=normalized_response,
            tool_schemas=state.tool_schemas,
        )
        return normalized_response, combined_text

    def _run_finalization_pass(self, state: ConversationState):
        """Ask the model for a final toolless answer after loop exhaustion."""
        state.summary_pass_used = True
        finalization_prompt = (
            state.system_prompt
            + "\n\nFinalization mode:\n"
            + "- Do not call tools.\n"
            + "- Use only information already present in the conversation.\n"
            + "- Provide the best final answer you can.\n"
            + "- If the work is incomplete, say what was completed and the next concrete step.\n"
        )
        finalization_messages = list(state.provider_messages)
        finalization_messages.append(
            {
                "role": "user",
                "content": (
                    "Finalize the response now. Do not call tools. "
                    "Use the existing context and tool results."
                ),
            }
        )
        finalization_messages, compression_meta = self.context_manager.compact_provider_messages(
            messages=finalization_messages,
            system_prompt=finalization_prompt,
            session_id=state.session_id,
            artifact_label="finalization-provider",
            tool_schemas=[],
        )
        if compression_meta.get("compressed_history"):
            self._maybe_reset_context_warning(
                state,
                {
                    "warning_tier": compression_meta.get("warning_tier", 0.0),
                },
            )
            self._record_turn_event(
                state.session_id,
                "finalization_context_compressed",
                "Compressed older context before the finalization pass.",
                estimated_tokens=compression_meta.get("estimated_tokens", 0),
                summarized_messages=compression_meta.get("summarized_messages", 0),
                kept_recent_messages=compression_meta.get("kept_recent_messages", 0),
                summary_chunk_count=compression_meta.get("summary_chunk_count", 0),
                pruned_tool_items=compression_meta.get("pruned_tool_items", 0),
            )

        streamed_chunks: List[str] = []
        response = None
        request_messages = self._snapshot_messages(finalization_messages)
        for provider_event in self.provider.stream_generate(
            system_prompt=finalization_prompt,
            messages=finalization_messages,
            tool_schemas=[],
        ):
            if provider_event.type == "text_delta" and provider_event.text:
                streamed_chunks.append(provider_event.text)
                yield AgentEvent(
                    type="text_delta",
                    text=provider_event.text,
                    payload={"model_round": state.model_round, "finalization_pass": True},
                )
            elif provider_event.type == "response":
                response = provider_event.response or ProviderResponse()

        combined_text = "".join(streamed_chunks)
        normalized_response = self._normalize_response(response)
        if combined_text and not normalized_response.content.strip():
            normalized_response.content = combined_text
        self._record_provider_round(
            state=state,
            phase="finalization",
            title="Finalization Pass",
            system_prompt=finalization_prompt,
            request_messages=request_messages,
            response=normalized_response,
            tool_schemas=[],
        )
        return normalized_response, combined_text

    def _build_state_events(self, *, user_message: str, mode: str, project_slug: str, session_id: str, agent_slug: str = ""):
        """Build the per-turn state before the loop starts, yielding visible preflight status events."""
        yield AgentEvent(
            type="status",
            text="Preparing next turn context: loading startup context.",
            payload={"phase": "turn_preflight", "step": "startup_context"},
        )
        self.context_manager.provider = self.provider
        self.memory_manager.set_provider(self.provider)
        self.research_workflow.provider = self.provider
        resolved_agent_slug = str(agent_slug or self._default_agent_slug_for_mode(mode)).strip()
        active_agent = self.agent_manager.get_agent(resolved_agent_slug)
        if active_agent is None:
            raise ValueError("Agent not found: %s" % resolved_agent_slug)
        context = self.context_manager.build_startup_context(
            mode=mode,
            project_slug=project_slug,
            session_id=session_id,
        )
        yield AgentEvent(
            type="status",
            text="Preparing next turn context: assembling tool, skill, MCP, and agent instructions.",
            payload={"phase": "turn_preflight", "step": "indexes_and_agent"},
        )
        exposure = getattr(self.config, "exposure", None)
        tools_include = list(getattr(exposure, "tools_include", []) or [])
        tools_exclude = list(getattr(exposure, "tools_exclude", []) or [])
        skills_include = list(getattr(exposure, "skills_include", []) or [])
        skills_exclude = list(getattr(exposure, "skills_exclude", []) or [])
        tool_schemas = collect_tool_schemas(
            self.tool_manager,
            mode=mode,
            include=tools_include,
            exclude=tools_exclude,
        )
        valid_tool_names = [item["name"] for item in tool_schemas]
        tool_index = self.tool_manager.build_prompt_index(
            limit=64,
            mode=mode,
            include=tools_include,
            exclude=tools_exclude,
        )
        skill_index = self.skill_manager.build_prompt_index(
            limit=64,
            include=skills_include,
            exclude=skills_exclude,
            agent_slug=resolved_agent_slug,
        )
        agent_summary = self.agent_manager.build_prompt_summary(resolved_agent_slug)
        agent_body = active_agent.runtime_body().strip()
        mcp_index = self.tool_manager.build_mcp_index(limit=6)
        research_workflow_snapshot: Dict[str, object] = {}
        research_runtime_context = ""
        system_prompt = build_system_prompt(
            mode=mode,
            project_slug=project_slug,
            context=context,
            tool_names=valid_tool_names,
            tool_index=tool_index,
            skill_index=skill_index,
            agent_summary=agent_summary,
            agent_body=agent_body,
            mcp_index=mcp_index,
            research_runtime_context=research_runtime_context,
        )
        yield AgentEvent(
            type="status",
            text="Preparing next turn context: compacting history and rebuilding the next model request.",
            payload={"phase": "turn_preflight", "step": "history_compaction"},
        )
        provider_messages, context_meta = self.context_manager.build_provider_messages(
            session_id=session_id,
            user_message=user_message,
            system_prompt=system_prompt,
            tool_schemas=tool_schemas,
        )
        self.session_store.append_message(
            session_id,
            "user",
            user_message,
            metadata={"mode": mode, "project_slug": project_slug},
        )

        state = ConversationState(
            user_message=user_message,
            mode=mode,
            project_slug=project_slug,
            session_id=session_id,
            agent_slug=resolved_agent_slug,
            system_prompt=system_prompt,
            provider_messages=provider_messages,
            tool_schemas=tool_schemas,
            runtime=self._build_runtime(mode=mode, project_slug=project_slug, session_id=session_id, agent_slug=resolved_agent_slug),
            budget=self._build_budget(),
            valid_tool_names=valid_tool_names,
            research_workflow_snapshot=research_workflow_snapshot,
        )
        self._append_turn_transcript(
            state,
            {
                "kind": "user_input",
                "content": user_message,
            },
        )
        self._record_turn_event(
            session_id,
            "turn_started",
            shorten(user_message, 120),
            mode=mode,
            project_slug=project_slug,
            agent_slug=resolved_agent_slug,
            context_tokens=context_meta.get("estimated_tokens", 0),
            compressed_history=context_meta.get("compressed_history", False),
            summarized_messages=context_meta.get("summarized_messages", 0),
        )
        if context_meta.get("compressed_history"):
            yield AgentEvent(
                type="status",
                text="Prepared next model request context after compressing older history.",
                payload={
                    "phase": "turn_preflight",
                    "step": "history_ready",
                    "compressed_history": True,
                    "summarized_messages": context_meta.get("summarized_messages", 0),
                    "kept_recent_messages": context_meta.get("kept_recent_messages", 0),
                },
            )
        else:
            yield AgentEvent(
                type="status",
                text="Prepared next model request context.",
                payload={
                    "phase": "turn_preflight",
                    "step": "history_ready",
                    "compressed_history": False,
                    "kept_recent_messages": context_meta.get("kept_recent_messages", 0),
                },
            )
        return state

    def run_conversation_events(self, *, user_message: str, mode: str, project_slug: str, session_id: str, agent_slug: str = ""):
        """Run the provider loop and yield incremental agent events."""
        state = yield from self._build_state_events(
            user_message=user_message,
            mode=mode,
            project_slug=project_slug,
            session_id=session_id,
            agent_slug=agent_slug,
        )
        final_already_streamed = False

        while state.model_round < state.budget.max_model_rounds:
            state.model_round += 1
            status_event = self._emit_status(
                state,
                "Model round %s" % state.model_round,
                model_round=state.model_round,
                tool_rounds=state.tool_rounds,
            )
            if status_event is not None:
                yield status_event

            try:
                status_event = self._emit_status(
                    state,
                    "Sending model request for round %s." % state.model_round,
                    model_round=state.model_round,
                    tool_rounds=state.tool_rounds,
                    phase="main_request",
                )
                if status_event is not None:
                    yield status_event
                response, streamed_text = yield from self._stream_provider_round(state)
                state.consecutive_errors = 0
                state.overflow_recovery_attempts = 0
            except Exception as exc:
                if self._is_context_overflow_error(exc):
                    recovered = self._recover_from_context_overflow(
                        state,
                        phase="main",
                        error_text=str(exc),
                    )
                    if recovered:
                        status_event = self._emit_status(
                            state,
                            "Context overflow detected; aggressively compacting history and retrying.",
                            overflow_recovery_attempt=state.overflow_recovery_attempts,
                        )
                        if status_event is not None:
                            yield status_event
                        continue
                state.consecutive_errors += 1
                error_text = "Provider round %s failed: %s" % (state.model_round, exc)
                self._record_turn_event(
                    state.session_id,
                    "provider_error",
                    error_text,
                    traceback=traceback.format_exc(limit=4),
                    consecutive_errors=state.consecutive_errors,
                )
                status_event = self._emit_status(
                    state,
                    error_text,
                    consecutive_errors=state.consecutive_errors,
                )
                if status_event is not None:
                    yield status_event
                if state.consecutive_errors >= state.budget.max_consecutive_errors:
                    state.final_reason = "provider_errors_exhausted"
                    break
                continue

            response = self._normalize_response(response)
            round_text = response.content.strip()
            round_streamed = bool((streamed_text or "").strip())

            if response.tool_calls:
                if round_text:
                    state.fallback_response_text = round_text
                    state.fallback_response_streamed = round_streamed

                prepared_calls, invalid_batch = self._prepare_tool_calls(state, response.tool_calls)
                repaired_calls = [item for item in prepared_calls if item.repaired_from]
                if repaired_calls:
                    repaired_text = ", ".join(
                        "%s->%s" % (item.repaired_from, item.name) for item in repaired_calls
                    )
                    status_event = self._emit_status(
                        state,
                        "Auto-repaired tool names: %s" % repaired_text,
                        repaired_count=len(repaired_calls),
                    )
                    if status_event is not None:
                        yield status_event

                assistant_tool_message = self._build_assistant_tool_message(response.content, prepared_calls)
                state.provider_messages.append(assistant_tool_message)
                self._append_turn_transcript(
                    state,
                    {
                        "kind": "assistant_tool_calls",
                        "content": response.content,
                        "tool_calls": [
                            {
                                "call_id": item.call_id,
                                "name": item.name,
                                "original_name": item.original_name,
                                "arguments": item.arguments,
                                "status": item.status,
                                "error": item.error,
                                "repaired_from": item.repaired_from,
                            }
                            for item in prepared_calls
                        ],
                    },
                )
                self.session_store.append_conversation_event(
                    state.session_id,
                    event_kind="assistant_tool_call",
                    role="assistant",
                    content=self._assistant_tool_event_content(response.content, prepared_calls),
                    payload={"message": assistant_tool_message},
                )
                if invalid_batch:
                    state.tool_validation_retries += 1
                    synthetic_results = [self._build_synthetic_tool_result(item) for item in prepared_calls]
                    for event in self._record_tool_results(state, synthetic_results):
                        yield event
                    status_event = self._emit_status(
                        state,
                        "Tool validation failed; asking the model to retry the tool batch.",
                        validation_retries=state.tool_validation_retries,
                    )
                    if status_event is not None:
                        yield status_event
                    if state.tool_validation_retries > state.budget.max_tool_validation_retries:
                        state.final_reason = "tool_validation_exhausted"
                        break
                    continue

                state.tool_validation_retries = 0
                executable_calls = [item for item in prepared_calls if item.status == "execute"]
                if not executable_calls:
                    synthetic_results = [self._build_synthetic_tool_result(item) for item in prepared_calls]
                    for event in self._record_tool_results(state, synthetic_results):
                        yield event
                    status_event = self._emit_status(
                        state,
                        "No executable tool calls remained after guardrails.",
                        skipped_calls=len(prepared_calls),
                    )
                    if status_event is not None:
                        yield status_event
                    continue

                if state.tool_rounds >= state.budget.max_tool_rounds:
                    state.final_reason = "tool_round_limit_reached"
                    status_event = self._emit_status(
                        state,
                        "Tool round limit reached; finalizing without more tool execution.",
                        max_tool_rounds=state.budget.max_tool_rounds,
                    )
                    if status_event is not None:
                        yield status_event
                    break

                state.tool_rounds += 1
                state.post_tool_nudge_used = False
                for item in executable_calls:
                    yield AgentEvent(
                        type="tool_call",
                        text=item.name,
                        payload={
                            "call_id": item.call_id,
                            "arguments": item.arguments,
                            "tool_round": state.tool_rounds,
                            "repaired_from": item.repaired_from,
                        },
                    )

                state.runtime["_tool_results_in_round"] = []
                execution_results = handle_function_calls(
                    self.tool_manager,
                    [
                        ProviderToolCall(
                            name=item.name,
                            arguments=item.arguments,
                            call_id=item.call_id,
                        )
                        for item in executable_calls
                    ],
                    state.runtime,
                )
                execution_by_call_id = {item["call_id"]: item for item in execution_results}
                merged_results = []
                for item in prepared_calls:
                    if item.status == "execute":
                        result = dict(execution_by_call_id[item.call_id])
                        if item.repaired_from and isinstance(result.get("output"), dict):
                            result["output"].setdefault("repaired_from", item.repaired_from)
                        merged_results.append(result)
                    else:
                        merged_results.append(self._build_synthetic_tool_result(item))

                for event in self._record_tool_results(state, merged_results):
                    yield event
                continue

            if round_text:
                state.final_text = round_text
                state.final_reason = "assistant_text"
                self._append_turn_transcript(
                    state,
                    {
                        "kind": "assistant_output",
                        "content": round_text,
                        "model_round": state.model_round,
                    },
                )
                final_already_streamed = round_streamed
                break

            if self._recent_messages_end_with_tool_result(state.provider_messages) and not state.post_tool_nudge_used:
                state.post_tool_nudge_used = True
                status_event = self._emit_status(
                    state,
                    "Empty response after tools; nudging the model to continue.",
                    model_round=state.model_round,
                )
                if status_event is not None:
                    yield status_event
                empty_message = {"role": "assistant", "content": "(empty)"}
                nudge_message = {
                    "role": "user",
                    "content": (
                        "You just executed tool calls but returned an empty response. "
                        "Please use the tool results above and continue with a final answer."
                    ),
                }
                state.provider_messages.append(empty_message)
                state.provider_messages.append(nudge_message)
                self.session_store.append_conversation_event(
                    state.session_id,
                    event_kind="internal_empty_response",
                    role="assistant",
                    content="(empty)",
                    payload={"message": empty_message},
                )
                self.session_store.append_conversation_event(
                    state.session_id,
                    event_kind="internal_nudge",
                    role="user",
                    content=str(nudge_message["content"]),
                    payload={"message": nudge_message},
                )
                continue

            if state.empty_response_retries < state.budget.max_empty_response_retries:
                state.empty_response_retries += 1
                status_event = self._emit_status(
                    state,
                    "Empty response from model; retrying.",
                    empty_response_retries=state.empty_response_retries,
                )
                if status_event is not None:
                    yield status_event
                continue

            if state.fallback_response_text:
                state.final_text = state.fallback_response_text
                state.final_reason = "fallback_content_with_tools"
                final_already_streamed = state.fallback_response_streamed
                break

            state.final_reason = "empty_response_exhausted"
            break

        if not state.final_text:
            status_event = self._emit_status(
                state,
                "Running a finalization pass to produce the best possible answer.",
                final_reason=state.final_reason or "loop_exit",
            )
            if status_event is not None:
                yield status_event
            try:
                final_response, streamed_text = yield from self._run_finalization_pass(state)
                final_text = self._normalize_text(final_response.content).strip()
                if final_text:
                    state.final_text = final_text
                    state.final_reason = state.final_reason or "finalization_pass"
                    self._append_turn_transcript(
                        state,
                        {
                            "kind": "assistant_output",
                            "content": final_text,
                            "source": "finalization_pass",
                            "model_round": state.model_round,
                        },
                    )
                    final_already_streamed = bool((streamed_text or "").strip())
                elif state.fallback_response_text:
                    state.final_text = state.fallback_response_text
                    state.final_reason = state.final_reason or "fallback_content_with_tools"
                    final_already_streamed = state.fallback_response_streamed
            except Exception as exc:
                if self._is_context_overflow_error(exc):
                    recovered = self._recover_from_context_overflow(
                        state,
                        phase="finalization",
                        error_text=str(exc),
                    )
                    if recovered:
                        try:
                            final_response, streamed_text = yield from self._run_finalization_pass(state)
                            final_text = self._normalize_text(final_response.content).strip()
                            if final_text:
                                state.final_text = final_text
                                state.final_reason = state.final_reason or "finalization_pass"
                                self._append_turn_transcript(
                                    state,
                                    {
                                        "kind": "assistant_output",
                                        "content": final_text,
                                        "source": "finalization_pass_retry",
                                        "model_round": state.model_round,
                                    },
                                )
                                final_already_streamed = bool((streamed_text or "").strip())
                                exc = None
                        except Exception as retry_exc:
                            exc = retry_exc
                if exc is not None:
                    self._record_turn_event(
                        state.session_id,
                        "finalization_error",
                        str(exc),
                        traceback=traceback.format_exc(limit=4),
                    )
                if exc is not None and state.fallback_response_text:
                    state.final_text = state.fallback_response_text
                    state.final_reason = state.final_reason or "fallback_content_with_tools"
                    final_already_streamed = state.fallback_response_streamed

        if not state.final_text:
            state.final_text = "Moonshine completed the request but could not assemble a stable final answer."
            state.final_reason = state.final_reason or "default_terminal"
            self._append_turn_transcript(
                state,
                {
                    "kind": "assistant_output",
                    "content": state.final_text,
                    "source": "default_terminal",
                    "model_round": state.model_round,
                },
            )
            final_already_streamed = False

        self.session_store.append_message(
            state.session_id,
            "assistant",
            state.final_text,
            metadata={
                "mode": state.mode,
                "project_slug": state.project_slug,
                "model_rounds": state.model_round,
                "tool_rounds": state.tool_rounds,
                "final_reason": state.final_reason,
                "summary_pass_used": state.summary_pass_used,
            },
        )
        if state.mode == "research" and not any(
            item.get("kind") == "assistant_output" and str(item.get("content") or "") == state.final_text
            for item in state.turn_transcript
        ):
            self._append_turn_transcript(
                state,
                {
                    "kind": "assistant_output",
                    "content": state.final_text,
                    "source": state.final_reason or "final",
                    "model_round": state.model_round,
                },
            )
        research_workflow_update: Dict[str, object] = {}
        if state.mode == "research":
            try:
                status_event = self._emit_status(
                    state,
                    "Archiving research progress from the completed turn.",
                    phase="research_archive",
                )
                if status_event is not None:
                    yield status_event
                archive_payload = self.research_workflow.archive_after_turn(
                    project_slug=state.project_slug,
                    session_id=state.session_id,
                    user_message=user_message,
                    assistant_message=state.final_text,
                    turn_context=list(state.turn_transcript),
                )
                research_workflow_update = {"research_log_archive": archive_payload}
                status_event = self._emit_status(
                    state,
                    "Research log archived: %s record(s)."
                    % int(archive_payload.get("archived") or 0),
                    research_workflow=research_workflow_update,
                )
                if status_event is not None:
                    yield status_event
                archive_status = dict(archive_payload or {})
                if archive_status.get("error"):
                    status_event = self._emit_status(
                        state,
                        "Research log archival failed: %s" % str(archive_status.get("error")),
                        phase="research_archive",
                        research_log_archive=archive_status,
                    )
                    if status_event is not None:
                        yield status_event
                elif archive_status.get("skipped"):
                    status_event = self._emit_status(
                        state,
                        "Research log archival skipped: %s" % str(archive_status.get("skipped")),
                        phase="research_archive",
                        research_log_archive=archive_status,
                    )
                    if status_event is not None:
                        yield status_event
            except Exception as exc:
                self._record_turn_event(
                    state.session_id,
                    "research_workflow_error",
                    str(exc),
                    traceback=traceback.format_exc(limit=4),
                )
        if state.mode != "research":
            extraction_result = self.memory_manager.submit_auto_extract(
                user_message,
                state.final_text,
                project_slug,
                session_id=session_id,
                pending_tool_calls=False,
            )
            if extraction_result.get("queued"):
                self._record_turn_event(
                    state.session_id,
                    "memory_extract_queued",
                    "Queued background memory extraction.",
                    task_id=extraction_result.get("task_id", ""),
                )
            elif extraction_result.get("entries") or extraction_result.get("conclusions"):
                self._record_turn_event(
                    state.session_id,
                    "memory_extracted",
                    "Persisted post-turn memory proposals.",
                    entries=extraction_result.get("entries", 0),
                    conclusions=extraction_result.get("conclusions", 0),
                    updated_files=list(extraction_result.get("updated_files") or []),
                )
        self._record_turn_event(
            state.session_id,
            "turn_completed",
            shorten(state.final_text, 160),
            model_rounds=state.model_round,
            tool_rounds=state.tool_rounds,
            final_reason=state.final_reason,
            summary_pass_used=state.summary_pass_used,
        )
        yield AgentEvent(
            type="final",
            text=state.final_text,
            payload={
                "model_rounds": state.model_round,
                "tool_rounds": state.tool_rounds,
                "reason": state.final_reason,
                "summary_pass_used": state.summary_pass_used,
                "research_workflow": research_workflow_update,
                "render_final": not final_already_streamed,
            },
        )

    def run_conversation(self, *, user_message: str, mode: str, project_slug: str, session_id: str, agent_slug: str = "") -> str:
        """Run the provider loop and return the final text."""
        final_text = ""
        for event in self.run_conversation_events(
            user_message=user_message,
            mode=mode,
            project_slug=project_slug,
            session_id=session_id,
            agent_slug=agent_slug,
        ):
            if event.type == "final":
                final_text = event.text
        return final_text


def build_terminal_parser() -> argparse.ArgumentParser:
    """Build a parser for direct terminal use."""
    parser = argparse.ArgumentParser(prog="python -m moonshine.run_agent")
    parser.add_argument("--home", default=None, help="Moonshine runtime home")
    parser.add_argument("--mode", default="chat", help="Conversation mode")
    parser.add_argument("--project", default=None, help="Active project slug")
    parser.add_argument("--session", default=None, help="Resume an existing session id and continue its full conversation history.")
    parser.add_argument(
        "--prompt",
        default="",
        help="Optional one-shot prompt. If omitted, an interactive terminal shell starts.",
    )
    parser.add_argument(
        "--auto-run",
        action="store_true",
        help="Deprecated compatibility flag. Research prompt runs autonomously by default.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="In research mode, run only one turn and wait for the next user input.",
    )
    parser.add_argument(
        "--no-auto-run",
        action="store_true",
        help="Alias for --interactive; disable default research autopilot for this prompt.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Maximum research autopilot iterations. Defaults to agent.research_max_iterations.",
    )
    return parser


def render_agent_events(events: Iterable[AgentEvent]) -> str:
    """Render agent events to the terminal and return the final text."""
    final_text = ""
    final_render = True
    emitted_text = False
    for event in events:
        if event.type == "status":
            if emitted_text:
                print()
                emitted_text = False
            print("[status] %s" % event.text)
        elif event.type == "tool_call":
            if emitted_text:
                print()
                emitted_text = False
            print("[tool] %s %s" % (event.text, json.dumps(event.payload.get("arguments", {}), ensure_ascii=False)))
        elif event.type == "tool_result":
            if emitted_text:
                print()
                emitted_text = False
            output = shorten(json.dumps(event.payload.get("output", {}), ensure_ascii=False), 220)
            print("[tool-result] %s %s" % (event.text, output))
        elif event.type == "tool_error":
            if emitted_text:
                print()
                emitted_text = False
            print("[tool-error] %s %s" % (event.text, event.payload.get("error", "unknown tool error")))
        elif event.type == "text_delta":
            print(event.text, end="", flush=True)
            emitted_text = True
        elif event.type == "final":
            final_text = event.text
            final_render = bool(event.payload.get("render_final", True))
    if emitted_text:
        print()
    elif final_text and final_render:
        print(final_text)
    return final_text


def _resolve_research_project_interactively(app, state, line: str) -> None:
    """Resolve a pending research project in the direct terminal runner."""
    if not getattr(state, "auto_project_pending", False):
        return
    result = app.prepare_research_project(line, state, allow_user_choice=True)
    if result.get("status") != "needs_choice":
        print("[research] Using project: %s" % result.get("project_slug"))
        return
    resolution = dict(result.get("resolution") or {})
    candidates = list(result.get("candidates") or [])
    print("[research] Similar existing projects found.")
    for index, candidate in enumerate(candidates, start=1):
        print(
            "  %s. %s (confidence %.2f): %s"
            % (
                index,
                candidate.get("slug", ""),
                float(candidate.get("confidence", 0.0) or 0.0),
                candidate.get("reason", ""),
            )
        )
    print("  n. Create new project: %s" % result.get("new_project_slug"))
    choice = input("Choose an existing project number, or press Enter for new: ").strip().lower()
    selected_slug = ""
    if choice.isdigit():
        index = int(choice) - 1
        if 0 <= index < len(candidates):
            selected_slug = str(candidates[index].get("slug", ""))
    final = app.finalize_research_project_choice(state, resolution, selected_project_slug=selected_slug)
    print("[research] Using project: %s" % final.get("project_slug"))


def run_terminal_session(
    *,
    home: Optional[str] = None,
    mode: str = "chat",
    project_slug: Optional[str] = None,
    session_id: Optional[str] = None,
    auto_run_research: bool = True,
    max_iterations: Optional[int] = None,
) -> int:
    """Run Moonshine directly in an interactive terminal session."""
    from moonshine.agent_runtime.display import render_banner
    from moonshine.app import MoonshineApp

    app = MoonshineApp(home=home)
    iteration_limit = int(max_iterations or app.config.agent.research_max_iterations)
    state = app.start_shell_state(mode=mode, project_slug=project_slug, session_id=session_id)
    print(render_banner(state.mode, state.project_slug, state.session_id))
    print("Type /help for commands. Type /exit to close the session.")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            app.close_session(state)
            print()
            return 0
        if not line:
            continue
        for notification in app.poll_memory_notifications(state):
            print(notification)

        result = app.execute_command(line, state)
        if result is None:
            _resolve_research_project_interactively(app, state, line)
            if state.mode == "research" and auto_run_research:
                render_agent_events(
                    app.run_research_autopilot_events(
                        line,
                        state,
                        max_iterations=iteration_limit,
                    )
                )
            else:
                render_agent_events(app.ask_stream(line, state))
            for notification in app.poll_memory_notifications(state):
                print(notification)
            continue
        if result == "EXIT":
            app.close_session(state)
            return 0
        print(result)


def main(argv: Optional[list] = None) -> int:
    """Entry point for running Moonshine directly from run_agent.py."""
    from moonshine.app import MoonshineApp

    parser = build_terminal_parser()
    args = parser.parse_args(argv)
    if args.prompt:
        app = MoonshineApp(home=args.home)
        state = app.start_shell_state(mode=args.mode, project_slug=args.project, session_id=args.session)
        should_auto_run = bool(args.auto_run) or (args.mode == "research" and not args.interactive and not args.no_auto_run)
        iteration_limit = int(args.max_iterations or app.config.agent.research_max_iterations)
        if should_auto_run:
            render_agent_events(
                app.run_research_autopilot_events(
                    args.prompt,
                    state,
                    max_iterations=iteration_limit,
                )
            )
        else:
            render_agent_events(app.ask_stream(args.prompt, state))
        for notification in app.poll_memory_notifications(state):
            print(notification)
        app.close_session(state)
        return 0
    return run_terminal_session(
        home=args.home,
        mode=args.mode,
        project_slug=args.project,
        session_id=args.session,
        auto_run_research=bool(args.auto_run) or (args.mode == "research" and not args.interactive and not args.no_auto_run),
        max_iterations=args.max_iterations,
    )


if __name__ == "__main__":
    raise SystemExit(main())
