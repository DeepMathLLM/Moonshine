"""Provider implementations for Moonshine."""

from __future__ import annotations

import json
import os
import time
from urllib.parse import quote
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional

from moonshine.agent_runtime.model_metadata import DEFAULT_CONTEXT_WINDOW_TOKENS
from moonshine.json_schema import JsonSchemaValidationError, validate_json_schema

try:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen
except ImportError:  # pragma: no cover
    Request = None
    urlopen = None
    HTTPError = Exception
    URLError = Exception


@dataclass
class ProviderToolCall:
    """Structured tool call returned by a provider."""

    name: str
    arguments: Dict[str, object]
    call_id: str = ""


@dataclass
class ProviderResponse:
    """Structured provider response."""

    content: str = ""
    tool_calls: List[ProviderToolCall] = field(default_factory=list)
    reasoning_content: str = ""
    raw_payload: Dict[str, object] = field(default_factory=dict)


@dataclass
class ProviderStreamEvent:
    """Incremental provider event."""

    type: str
    text: str = ""
    response: Optional[ProviderResponse] = None
    raw_payload: Dict[str, object] = field(default_factory=dict)


def _coerce_structured_payload(parsed: Any, response_schema: Dict[str, object]) -> Dict[str, object]:
    """Coerce common provider JSON-mode shape drift before schema validation."""
    if isinstance(parsed, list) and str(response_schema.get("type") or "") == "object":
        properties = dict(response_schema.get("properties") or {})
        required = list(response_schema.get("required") or [])
        if len(properties) == 1 and len(required) == 1:
            key = str(required[0])
            property_schema = dict(properties.get(key) or {})
            if str(property_schema.get("type") or "") == "array":
                parsed = {key: parsed}
    validate_json_schema(parsed, response_schema)
    return dict(parsed)


def _parse_json_object_from_text(text: str) -> Dict[str, object]:
    """Parse a JSON object, accepting simple prose/fence wrappers as fallback."""
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        parsed = json.loads(cleaned)
    except ValueError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("structured provider response must be a JSON object")
    return dict(parsed)


class BaseProvider(object, metaclass=ABCMeta):
    """Abstract response provider."""

    @abstractmethod
    def generate(
        self,
        *,
        system_prompt: str,
        messages: List[Dict[str, str]],
        tool_schemas: Optional[List[Dict[str, object]]] = None,
    ) -> ProviderResponse:
        """Generate a provider response."""
        raise NotImplementedError

    def stream_generate(
        self,
        *,
        system_prompt: str,
        messages: List[Dict[str, str]],
        tool_schemas: Optional[List[Dict[str, object]]] = None,
    ) -> Iterator[ProviderStreamEvent]:
        """Stream provider output. Providers may override for true token streaming."""
        response = self.generate(
            system_prompt=system_prompt,
            messages=messages,
            tool_schemas=tool_schemas,
        )
        if str(response.reasoning_content or "").strip():
            yield ProviderStreamEvent(type="reasoning_delta", text=response.reasoning_content, raw_payload=response.raw_payload)
        if response.content:
            yield ProviderStreamEvent(type="text_delta", text=response.content, raw_payload=response.raw_payload)
        yield ProviderStreamEvent(type="response", response=response, raw_payload=response.raw_payload)

    def generate_structured(
        self,
        *,
        system_prompt: str,
        messages: List[Dict[str, str]],
        response_schema: Dict[str, object],
        schema_name: str,
    ) -> Dict[str, object]:
        """Generate a structured JSON object, validating it locally."""
        response = self.generate(system_prompt=system_prompt, messages=messages, tool_schemas=[])
        parsed = json.loads((response.content or "").strip())
        return _coerce_structured_payload(parsed, response_schema)


def _chunk_text(text: str, chunk_size: int = 32) -> Iterator[str]:
    """Split text into small streaming chunks."""
    for index in range(0, len(text), chunk_size):
        yield text[index : index + chunk_size]


def _safe_parse_tool_arguments(raw_arguments: str) -> Dict[str, object]:
    """Parse tool arguments defensively."""
    cleaned = (raw_arguments or "").strip()
    if not cleaned:
        return {}
    try:
        parsed = json.loads(cleaned)
    except ValueError:
        return {"_raw_arguments": cleaned}
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


def _read_http_error_body(exc: Exception) -> str:
    """Best-effort extraction of an HTTP error response body."""
    if not isinstance(exc, HTTPError):
        return ""
    cached = getattr(exc, "_moonshine_error_body", None)
    if cached is not None:
        return str(cached)
    try:
        raw = exc.read()
    except Exception:
        return ""
    if not raw:
        return ""
    try:
        decoded = raw.decode("utf-8", errors="replace").strip()
    except Exception:
        decoded = str(raw)
    try:
        setattr(exc, "_moonshine_error_body", decoded)
    except Exception:
        pass
    return decoded


def _format_provider_exception(exc: Exception) -> str:
    """Produce a more useful provider error description."""
    if isinstance(exc, HTTPError):
        body = _read_http_error_body(exc)
        if body:
            return "HTTP Error %s: %s | response body: %s" % (exc.code, exc.reason, body)
        return "HTTP Error %s: %s" % (exc.code, exc.reason)
    return str(exc)


class OfflineProvider(BaseProvider):
    """Deterministic offline fallback provider."""

    def __init__(self, note: Optional[str] = None):
        self.note = note

    def _build_content(self, messages: List[Dict[str, str]], tool_schemas: Optional[List[Dict[str, object]]] = None) -> str:
        """Build a deterministic fallback response."""
        last_user = ""
        for item in reversed(messages):
            if item.get("role") == "user":
                last_user = item.get("content", "")
                break

        lines = ["Moonshine processed the request in offline mode."]
        if self.note:
            lines.append("Provider note: %s" % self.note)
        if last_user:
            lines.append("User request: %s" % last_user)
        if tool_schemas:
            lines.append("Registered tools: %s" % ", ".join(tool["name"] for tool in tool_schemas[:8]))
        lines.append("This response is intentionally conservative so the CLI, storage, and memory pipeline remain usable without an external model.")
        return "\n".join(lines).strip()

    def generate(self, *, system_prompt: str, messages: List[Dict[str, str]], tool_schemas: Optional[List[Dict[str, object]]] = None) -> ProviderResponse:
        content = self._build_content(messages, tool_schemas=tool_schemas)
        return ProviderResponse(content=content)

    def stream_generate(
        self,
        *,
        system_prompt: str,
        messages: List[Dict[str, str]],
        tool_schemas: Optional[List[Dict[str, object]]] = None,
    ) -> Iterator[ProviderStreamEvent]:
        """Stream fallback text in small chunks."""
        response = self.generate(
            system_prompt=system_prompt,
            messages=messages,
            tool_schemas=tool_schemas,
        )
        for chunk in _chunk_text(response.content):
            yield ProviderStreamEvent(type="text_delta", text=chunk)
        yield ProviderStreamEvent(type="response", response=response)

    def generate_structured(
        self,
        *,
        system_prompt: str,
        messages: List[Dict[str, str]],
        response_schema: Dict[str, object],
        schema_name: str,
    ) -> Dict[str, object]:
        raise RuntimeError("offline provider does not support structured generation")


class _OpenAIChatStreamBuilder(object):
    """Accumulate streamed chat-completions deltas."""

    def __init__(self):
        self.content_parts: List[str] = []
        self.reasoning_parts: List[str] = []
        self.tool_call_parts: Dict[int, Dict[str, str]] = {}

    def add_content(self, text: str) -> None:
        """Append streamed text."""
        if text:
            self.content_parts.append(text)

    def add_reasoning(self, text: str) -> None:
        """Append streamed reasoning content when an OpenAI-compatible provider returns it."""
        if text:
            self.reasoning_parts.append(text)

    def add_tool_delta(self, item: Dict[str, object]) -> None:
        """Accumulate incremental tool-call data."""
        index = int(item.get("index", 0))
        state = self.tool_call_parts.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if item.get("id"):
            state["id"] = str(item["id"])
        function = item.get("function") or {}
        if function.get("name"):
            state["name"] += str(function["name"])
        if function.get("arguments"):
            state["arguments"] += str(function["arguments"])

    def to_response(self) -> ProviderResponse:
        """Build the final response object."""
        tool_calls = []
        for index in sorted(self.tool_call_parts):
            item = self.tool_call_parts[index]
            tool_calls.append(
                ProviderToolCall(
                    name=item["name"],
                    arguments=_safe_parse_tool_arguments(item["arguments"]),
                    call_id=item["id"],
                )
            )
        return ProviderResponse(
            content="".join(self.content_parts),
            tool_calls=tool_calls,
            reasoning_content="".join(self.reasoning_parts),
            raw_payload={"streamed": True},
        )


class OpenAIChatCompletionsProvider(BaseProvider):
    """Minimal OpenAI-compatible chat completions client."""

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key_env: str,
        timeout_seconds: int,
        temperature: float,
        reasoning_effort: str = "",
        stream: bool = True,
        max_retries: int = 2,
        retry_backoff_seconds: float = 1.0,
        max_context_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS,
        structured_output_format: str = "json_schema",
        structured_output_format_callback: Optional[Callable[[str], None]] = None,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.reasoning_effort = str(reasoning_effort or "").strip()
        self.stream = stream
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self.max_context_tokens = max(1024, int(max_context_tokens))
        self.structured_output_format = str(structured_output_format or "json_schema").strip().lower()
        if self.structured_output_format not in {"auto", "json_schema", "json_object", "prompt"}:
            self.structured_output_format = "json_schema"
        self.structured_output_format_callback = structured_output_format_callback

    def _fallback(self, note: str) -> OfflineProvider:
        """Create an offline fallback provider."""
        return OfflineProvider(note=note)

    def _build_payload(
        self,
        *,
        system_prompt: str,
        messages: List[Dict[str, str]],
        tool_schemas: Optional[List[Dict[str, object]]] = None,
        stream: bool = False,
        response_format: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        """Build a chat-completions payload."""
        payload: Dict[str, object] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt}] + messages,
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        if tool_schemas:
            payload["tools"] = [{"type": "function", "function": schema} for schema in tool_schemas]
        if response_format:
            payload["response_format"] = response_format
        if stream:
            payload["stream"] = True
        return payload

    def _json_schema_response_format(self, *, schema_name: str, response_schema: Dict[str, object]) -> Dict[str, object]:
        """Return the strict OpenAI JSON-schema response format."""
        return {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": response_schema,
            },
        }

    def _json_object_response_format(self) -> Dict[str, object]:
        """Return the broad JSON-object response format used by DeepSeek and older compatible APIs."""
        return {"type": "json_object"}

    def _json_object_format_instruction(self, schema_name: str, response_schema: Dict[str, object]) -> str:
        """Build explicit JSON-mode instructions for providers without JSON-schema support."""
        return (
            "\n\nJSON mode fallback instructions:\n"
            "- Return exactly one valid JSON object and no markdown fences or explanatory prose.\n"
            "- The response must satisfy this JSON schema named `%s`:\n```json\n%s\n```\n"
        ) % (
            schema_name,
            json.dumps(response_schema, ensure_ascii=False, indent=2, sort_keys=True),
        )

    def _structured_format_attempt_order(self) -> List[str]:
        """Return structured-output formats to try, beginning with the configured mode."""
        mode = str(self.structured_output_format or "json_schema").strip().lower()
        if mode == "json_object":
            return ["json_object", "json_schema", "prompt"]
        if mode == "prompt":
            return ["prompt"]
        return ["json_schema", "json_object", "prompt"]

    def _build_structured_payload_for_format(
        self,
        *,
        system_prompt: str,
        messages: List[Dict[str, str]],
        response_schema: Dict[str, object],
        schema_name: str,
        format_name: str,
    ) -> Dict[str, object]:
        """Build a structured chat-completions payload for one output format."""
        response_format = None
        prompt = system_prompt
        if format_name == "json_schema":
            response_format = self._json_schema_response_format(
                schema_name=schema_name,
                response_schema=response_schema,
            )
        elif format_name == "json_object":
            response_format = self._json_object_response_format()
            prompt = system_prompt + self._json_object_format_instruction(schema_name, response_schema)
        elif format_name == "prompt":
            prompt = system_prompt + self._json_object_format_instruction(schema_name, response_schema)
        else:
            response_format = self._json_schema_response_format(
                schema_name=schema_name,
                response_schema=response_schema,
            )
        return self._build_payload(
            system_prompt=prompt,
            messages=messages,
            tool_schemas=[],
            response_format=response_format,
        )

    def _set_successful_structured_output_format(self, format_name: str) -> None:
        """Remember a fallback format once a structured call succeeds."""
        normalized = str(format_name or "").strip().lower()
        if normalized not in {"json_schema", "json_object", "prompt"}:
            return
        self.structured_output_format = normalized
        callback = getattr(self, "structured_output_format_callback", None)
        if callback:
            callback(normalized)

    def _retry_payload_with_json_object(
        self,
        payload: Dict[str, object],
        *,
        schema_name: str,
        response_schema: Dict[str, object],
    ) -> Dict[str, object]:
        """Clone a structured payload using JSON-object mode instead of JSON-schema mode."""
        retry_payload = dict(payload)
        retry_payload["response_format"] = self._json_object_response_format()
        messages = [dict(item) for item in list(retry_payload.get("messages") or []) if isinstance(item, dict)]
        format_instruction = self._json_object_format_instruction(schema_name, response_schema)
        if messages and str(messages[0].get("role") or "") == "system":
            system_content = str(messages[0].get("content") or "")
            messages[0]["content"] = (
                system_content
                + format_instruction
            )
        else:
            messages.insert(
                0,
                {
                    "role": "system",
                    "content": format_instruction.strip(),
                },
            )
        retry_payload["messages"] = messages
        return retry_payload

    def _should_retry_with_alternate_response_format(self, exc: Exception) -> bool:
        """Return True when a compatible API rejects the current structured format."""
        if not isinstance(exc, HTTPError) or getattr(exc, "code", None) != 400:
            return False
        lowered = _read_http_error_body(exc).lower()
        mentions_structured_format = (
            "response_format" in lowered
            or "json_schema" in lowered
            or "json_object" in lowered
            or "text.format" in lowered
        )
        mentions_incompatibility = (
            "unavailable" in lowered
            or "unsupported" in lowered
            or "unknown parameter" in lowered
            or "invalid" in lowered
            or "not support" in lowered
            or "does not support" in lowered
        )
        return (
            mentions_structured_format
            and mentions_incompatibility
        )

    def _should_retry_with_json_object_response_format(self, exc: Exception) -> bool:
        """Backward-compatible wrapper for older tests and callers."""
        return self._should_retry_with_alternate_response_format(exc)

    def _parse_structured_payload(self, parsed_payload: Dict[str, object], response_schema: Dict[str, object]) -> Dict[str, object]:
        """Parse and locally validate a structured chat-completions response."""
        message = parsed_payload["choices"][0]["message"]
        parsed = json.loads((message.get("content", "") or "").strip())
        return _coerce_structured_payload(parsed, response_schema)

    def _make_request(self, payload: Dict[str, object], api_key: str) -> Request:
        """Build a request object for the OpenAI-compatible endpoint."""
        return Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer %s" % api_key,
            },
            method="POST",
        )

    def _response_from_payload(self, parsed: Dict[str, object]) -> ProviderResponse:
        """Convert a chat-completions response payload into a ProviderResponse."""
        choice = parsed["choices"][0]["message"]
        tool_calls = []
        for item in choice.get("tool_calls", []) or []:
            function = item.get("function") or {}
            tool_calls.append(
                ProviderToolCall(
                    name=str(function.get("name", "")),
                    arguments=_safe_parse_tool_arguments(str(function.get("arguments", ""))),
                    call_id=str(item.get("id", "")),
                )
            )
        return ProviderResponse(
            content=choice.get("content", "") or "",
            tool_calls=tool_calls,
            reasoning_content=choice.get("reasoning_content", "") or "",
            raw_payload=parsed,
        )

    def generate(
        self,
        *,
        system_prompt: str,
        messages: List[Dict[str, str]],
        tool_schemas: Optional[List[Dict[str, object]]] = None,
    ) -> ProviderResponse:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            return self._fallback("missing API key environment variable %s" % self.api_key_env).generate(
                system_prompt=system_prompt,
                messages=messages,
                tool_schemas=tool_schemas,
            )

        if Request is None or urlopen is None:
            return self._fallback("urllib request support is unavailable").generate(
                system_prompt=system_prompt,
                messages=messages,
                tool_schemas=tool_schemas,
            )

        payload = self._build_payload(system_prompt=system_prompt, messages=messages, tool_schemas=tool_schemas)
        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                request = self._make_request(payload, api_key)
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    parsed = json.loads(response.read().decode("utf-8"))
                return self._response_from_payload(parsed)
            except (HTTPError, URLError, KeyError, IndexError, ValueError) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff_seconds * float(attempt + 1))
                    continue
        return self._fallback("chat completions call failed: %s" % _format_provider_exception(last_exc)).generate(
            system_prompt=system_prompt,
            messages=messages,
            tool_schemas=tool_schemas,
        )

    def stream_generate(
        self,
        *,
        system_prompt: str,
        messages: List[Dict[str, str]],
        tool_schemas: Optional[List[Dict[str, object]]] = None,
    ) -> Iterator[ProviderStreamEvent]:
        """Stream chat-completions content and tool-call deltas."""
        if not self.stream:
            yield from super().stream_generate(
                system_prompt=system_prompt,
                messages=messages,
                tool_schemas=tool_schemas,
            )
            return

        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            yield from self._fallback("missing API key environment variable %s" % self.api_key_env).stream_generate(
                system_prompt=system_prompt,
                messages=messages,
                tool_schemas=tool_schemas,
            )
            return

        if Request is None or urlopen is None:
            yield from self._fallback("urllib request support is unavailable").stream_generate(
                system_prompt=system_prompt,
                messages=messages,
                tool_schemas=tool_schemas,
            )
            return

        payload = self._build_payload(
            system_prompt=system_prompt,
            messages=messages,
            tool_schemas=tool_schemas,
            stream=True,
        )
        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                request = self._make_request(payload, api_key)
                builder = _OpenAIChatStreamBuilder()
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    for raw_line in response:
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data:
                            continue
                        if data == "[DONE]":
                            break
                        parsed = json.loads(data)
                        choice = (parsed.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}
                        content = delta.get("content") or ""
                        if isinstance(content, str) and content:
                            builder.add_content(content)
                            yield ProviderStreamEvent(type="text_delta", text=content, raw_payload=parsed)
                        reasoning_content = delta.get("reasoning_content") or ""
                        if isinstance(reasoning_content, str) and reasoning_content.strip():
                            builder.add_reasoning(reasoning_content)
                            yield ProviderStreamEvent(type="reasoning_delta", text=reasoning_content, raw_payload=parsed)
                        for tool_item in delta.get("tool_calls") or []:
                            builder.add_tool_delta(tool_item)
                response_payload = builder.to_response()
                yield ProviderStreamEvent(type="response", response=response_payload, raw_payload=response_payload.raw_payload)
                return
            except (HTTPError, URLError, KeyError, IndexError, ValueError) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff_seconds * float(attempt + 1))
                    continue
        # Some deployments reject stream=True while still supporting ordinary
        # chat completions. Try one non-stream request before falling back.
        response = self.generate(
            system_prompt=system_prompt,
            messages=messages,
            tool_schemas=tool_schemas,
        )
        if not (
            isinstance(response.content, str)
            and response.content.startswith("Moonshine processed the request in offline mode.")
        ):
            if response.content:
                yield ProviderStreamEvent(type="text_delta", text=response.content, raw_payload=response.raw_payload)
            yield ProviderStreamEvent(type="response", response=response, raw_payload=response.raw_payload)
            return
        yield from self._fallback(
            "streaming chat completions call failed: %s" % _format_provider_exception(last_exc)
        ).stream_generate(
            system_prompt=system_prompt,
            messages=messages,
            tool_schemas=tool_schemas,
        )

    def generate_structured(
        self,
        *,
        system_prompt: str,
        messages: List[Dict[str, str]],
        response_schema: Dict[str, object],
        schema_name: str,
    ) -> Dict[str, object]:
        """Generate structured JSON using response_format when supported."""
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError("missing API key environment variable %s" % self.api_key_env)
        if Request is None or urlopen is None:
            raise RuntimeError("urllib request support is unavailable")

        format_order = self._structured_format_attempt_order()
        format_index = 0
        format_name = format_order[format_index]
        initial_format_name = format_name
        payload = self._build_structured_payload_for_format(
            system_prompt=system_prompt,
            messages=messages,
            response_schema=response_schema,
            schema_name=schema_name,
            format_name=format_name,
        )
        attempts_remaining = self.max_retries + 1
        last_exc = None
        while attempts_remaining > 0:
            try:
                request = self._make_request(payload, api_key)
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    parsed_payload = json.loads(response.read().decode("utf-8"))
                result = self._parse_structured_payload(parsed_payload, response_schema)
                if format_name != initial_format_name or self.structured_output_format not in {"auto", format_name}:
                    self._set_successful_structured_output_format(format_name)
                return result
            except (HTTPError, URLError, KeyError, IndexError, ValueError, JsonSchemaValidationError) as exc:
                last_exc = exc
                attempts_remaining -= 1
                if (
                    self._should_retry_with_alternate_response_format(exc)
                    and format_index + 1 < len(format_order)
                ):
                    format_index += 1
                    format_name = format_order[format_index]
                    payload = self._build_structured_payload_for_format(
                        system_prompt=system_prompt,
                        messages=messages,
                        response_schema=response_schema,
                        schema_name=schema_name,
                        format_name=format_name,
                    )
                    if attempts_remaining <= 0:
                        attempts_remaining = 1
                    continue
                if attempts_remaining > 0:
                    time.sleep(self.retry_backoff_seconds * float(self.max_retries - attempts_remaining + 1))
                    continue
        raise RuntimeError("structured chat completions call failed: %s" % _format_provider_exception(last_exc))


class AzureOpenAIChatCompletionsProvider(OpenAIChatCompletionsProvider):
    """Azure OpenAI chat-completions provider using deployment-scoped REST URLs."""

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key_env: str,
        api_version: str,
        timeout_seconds: int,
        temperature: Optional[float],
        reasoning_effort: str = "",
        stream: bool = True,
        max_retries: int = 2,
        retry_backoff_seconds: float = 1.0,
        max_context_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS,
        structured_output_format: str = "json_schema",
        structured_output_format_callback: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(
            model=model,
            base_url=base_url,
            api_key_env=api_key_env,
            timeout_seconds=timeout_seconds,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            stream=stream,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            max_context_tokens=max_context_tokens,
            structured_output_format=structured_output_format,
            structured_output_format_callback=structured_output_format_callback,
        )
        self.api_version = (api_version or "2024-12-01-preview").strip()

    def _should_retry_without_temperature(self, payload: Dict[str, object], exc: Exception) -> bool:
        """Return True when Azure rejects an explicit temperature parameter."""
        if "temperature" not in payload:
            return False
        if not isinstance(exc, HTTPError) or getattr(exc, "code", None) != 400:
            return False
        lowered = _read_http_error_body(exc).lower()
        return (
            "temperature" in lowered
            and (
                "unsupported_value" in lowered
                or "does not support" in lowered
                or "only the default (1) value is supported" in lowered
                or "\"param\": \"temperature\"" in lowered
            )
        )

    def _retry_payload_without_temperature(self, payload: Dict[str, object]) -> Dict[str, object]:
        """Clone a payload without the explicit temperature field."""
        retry_payload = dict(payload)
        retry_payload.pop("temperature", None)
        return retry_payload

    def generate(
        self,
        *,
        system_prompt: str,
        messages: List[Dict[str, str]],
        tool_schemas: Optional[List[Dict[str, object]]] = None,
    ) -> ProviderResponse:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            return self._fallback("missing API key environment variable %s" % self.api_key_env).generate(
                system_prompt=system_prompt,
                messages=messages,
                tool_schemas=tool_schemas,
            )

        if Request is None or urlopen is None:
            return self._fallback("urllib request support is unavailable").generate(
                system_prompt=system_prompt,
                messages=messages,
                tool_schemas=tool_schemas,
            )

        payload = self._build_payload(system_prompt=system_prompt, messages=messages, tool_schemas=tool_schemas)
        attempts_remaining = self.max_retries + 1
        retried_without_temperature = False
        last_exc = None
        while attempts_remaining > 0:
            try:
                request = self._make_request(payload, api_key)
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    parsed = json.loads(response.read().decode("utf-8"))
                return self._response_from_payload(parsed)
            except (HTTPError, URLError, KeyError, IndexError, ValueError) as exc:
                last_exc = exc
                attempts_remaining -= 1
                if (not retried_without_temperature) and self._should_retry_without_temperature(payload, exc):
                    payload = self._retry_payload_without_temperature(payload)
                    retried_without_temperature = True
                    if attempts_remaining <= 0:
                        attempts_remaining = 1
                    continue
                if attempts_remaining > 0:
                    time.sleep(self.retry_backoff_seconds * float(self.max_retries - attempts_remaining + 1))
                    continue
        return self._fallback("chat completions call failed: %s" % _format_provider_exception(last_exc)).generate(
            system_prompt=system_prompt,
            messages=messages,
            tool_schemas=tool_schemas,
        )

    def generate_structured(
        self,
        *,
        system_prompt: str,
        messages: List[Dict[str, str]],
        response_schema: Dict[str, object],
        schema_name: str,
    ) -> Dict[str, object]:
        """Generate structured JSON and retry without temperature when needed."""
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError("missing API key environment variable %s" % self.api_key_env)
        if Request is None or urlopen is None:
            raise RuntimeError("urllib request support is unavailable")

        format_order = self._structured_format_attempt_order()
        format_index = 0
        format_name = format_order[format_index]
        initial_format_name = format_name
        payload = self._build_structured_payload_for_format(
            system_prompt=system_prompt,
            messages=messages,
            response_schema=response_schema,
            schema_name=schema_name,
            format_name=format_name,
        )
        attempts_remaining = self.max_retries + 1
        retried_without_temperature = False
        last_exc = None
        while attempts_remaining > 0:
            try:
                request = self._make_request(payload, api_key)
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    parsed_payload = json.loads(response.read().decode("utf-8"))
                result = self._parse_structured_payload(parsed_payload, response_schema)
                if format_name != initial_format_name or self.structured_output_format not in {"auto", format_name}:
                    self._set_successful_structured_output_format(format_name)
                return result
            except (HTTPError, URLError, KeyError, IndexError, ValueError, JsonSchemaValidationError) as exc:
                last_exc = exc
                attempts_remaining -= 1
                if (
                    self._should_retry_with_alternate_response_format(exc)
                    and format_index + 1 < len(format_order)
                ):
                    format_index += 1
                    format_name = format_order[format_index]
                    payload = self._build_structured_payload_for_format(
                        system_prompt=system_prompt,
                        messages=messages,
                        response_schema=response_schema,
                        schema_name=schema_name,
                        format_name=format_name,
                    )
                    if attempts_remaining <= 0:
                        attempts_remaining = 1
                    continue
                if (not retried_without_temperature) and self._should_retry_without_temperature(payload, exc):
                    payload = self._retry_payload_without_temperature(payload)
                    retried_without_temperature = True
                    if attempts_remaining <= 0:
                        attempts_remaining = 1
                    continue
                if attempts_remaining > 0:
                    time.sleep(self.retry_backoff_seconds * float(self.max_retries - attempts_remaining + 1))
                    continue
        raise RuntimeError("structured chat completions call failed: %s" % _format_provider_exception(last_exc))

    def _make_request(self, payload: Dict[str, object], api_key: str) -> Request:
        """Build an Azure deployment-scoped chat-completions request."""
        deployment = quote(str(self.model), safe="")
        api_version = quote(str(self.api_version), safe="")
        request_payload = dict(payload)
        request_payload.pop("model", None)
        # Azure deployments may reject request-only budgeting fields that Moonshine
        # uses locally for context planning. Keep them out of the wire payload.
        request_payload.pop("max_context_tokens", None)
        url = (
            "%s/openai/deployments/%s/chat/completions?api-version=%s"
            % (self.base_url.rstrip("/"), deployment, api_version)
        )
        return Request(
            url,
            data=json.dumps(request_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "api-key": api_key,
            },
            method="POST",
        )


class _OpenAIResponsesStreamBuilder(object):
    """Accumulate OpenAI Responses API streaming events."""

    def __init__(self):
        self.content_parts: List[str] = []
        self.reasoning_parts: List[str] = []
        self.tool_call_parts: Dict[str, Dict[str, str]] = {}
        self.completed_response: Dict[str, object] = {}

    def add_content(self, text: str) -> None:
        if text:
            self.content_parts.append(text)

    def add_reasoning(self, text: str) -> None:
        if text:
            self.reasoning_parts.append(text)

    def _tool_key(self, data: Dict[str, object], item: Optional[Dict[str, object]] = None) -> str:
        item = dict(item or {})
        return str(item.get("id") or data.get("item_id") or data.get("output_index") or len(self.tool_call_parts))

    def add_tool_item(self, data: Dict[str, object], item: Dict[str, object]) -> None:
        if str(item.get("type") or "") != "function_call":
            return
        key = self._tool_key(data, item)
        state = self.tool_call_parts.setdefault(key, {"name": "", "arguments": "", "call_id": ""})
        if item.get("name"):
            state["name"] = str(item.get("name") or "")
        if item.get("arguments") is not None:
            state["arguments"] = str(item.get("arguments") or "")
        if item.get("call_id") or item.get("id"):
            state["call_id"] = str(item.get("call_id") or item.get("id") or "")

    def add_tool_arguments_delta(self, data: Dict[str, object]) -> None:
        key = self._tool_key(data)
        state = self.tool_call_parts.setdefault(key, {"name": "", "arguments": "", "call_id": ""})
        if data.get("delta"):
            state["arguments"] += str(data.get("delta") or "")

    def add_tool_arguments_done(self, data: Dict[str, object]) -> None:
        key = self._tool_key(data)
        state = self.tool_call_parts.setdefault(key, {"name": "", "arguments": "", "call_id": ""})
        if data.get("arguments") is not None:
            state["arguments"] = str(data.get("arguments") or "")

    def to_response(self) -> ProviderResponse:
        if self.completed_response:
            response = OpenAIResponsesProvider._response_from_payload_static(self.completed_response)
            if not response.content and self.content_parts:
                response.content = "".join(self.content_parts)
            if not response.reasoning_content and self.reasoning_parts:
                response.reasoning_content = "".join(self.reasoning_parts)
            if not response.tool_calls and self.tool_call_parts:
                response.tool_calls = self._tool_calls()
            return response
        return ProviderResponse(
            content="".join(self.content_parts),
            reasoning_content="".join(self.reasoning_parts),
            tool_calls=self._tool_calls(),
            raw_payload={"streamed": True},
        )

    def _tool_calls(self) -> List[ProviderToolCall]:
        calls = []
        for key in sorted(self.tool_call_parts):
            item = self.tool_call_parts[key]
            if not item.get("name"):
                continue
            calls.append(
                ProviderToolCall(
                    name=item.get("name", ""),
                    arguments=_safe_parse_tool_arguments(item.get("arguments", "")),
                    call_id=item.get("call_id", "") or key,
                )
            )
        return calls


class OpenAIResponsesProvider(BaseProvider):
    """OpenAI-compatible Responses API provider."""

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key_env: str,
        timeout_seconds: int = 600,
        temperature: Optional[float] = None,
        reasoning_effort: str = "",
        reasoning_summary: str = "",
        structured_output_format: str = "json_schema",
        stream: bool = True,
        max_retries: int = 2,
        retry_backoff_seconds: float = 1.0,
        max_context_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS,
        structured_output_format_callback: Optional[Callable[[str], None]] = None,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.reasoning_effort = str(reasoning_effort or "").strip()
        self.reasoning_summary = str(reasoning_summary or "").strip()
        self.structured_output_format = str(structured_output_format or "json_schema").strip().lower()
        self.stream = bool(stream)
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self.max_context_tokens = max(1024, int(max_context_tokens))
        self.structured_output_format_callback = structured_output_format_callback

    def _fallback(self, note: str) -> OfflineProvider:
        return OfflineProvider(note=note)

    def _message_content_text(self, item: Dict[str, object]) -> str:
        content = str(item.get("content") or "")
        reasoning_content = str(item.get("reasoning_content") or "").strip()
        if reasoning_content:
            return "[reasoning]\n%s\n[/reasoning]\n\n%s" % (reasoning_content, content)
        return content

    def _responses_input(self, messages: List[Dict[str, object]]) -> List[Dict[str, object]]:
        items: List[Dict[str, object]] = []
        for raw in messages or []:
            item = dict(raw or {})
            role = str(item.get("role") or "user")
            if role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": str(item.get("tool_call_id") or item.get("name") or ""),
                        "output": str(item.get("content") or ""),
                    }
                )
                continue
            if role in {"user", "assistant", "system", "developer"}:
                text = self._message_content_text(item)
                if text:
                    normalized_role = "user" if role in {"system", "developer"} else role
                    items.append({"role": normalized_role, "content": text})
                for tool_call in list(item.get("tool_calls") or []):
                    tool_call = dict(tool_call or {})
                    function = dict(tool_call.get("function") or {})
                    name = str(function.get("name") or "")
                    if not name:
                        continue
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": str(tool_call.get("id") or ""),
                            "name": name,
                            "arguments": str(function.get("arguments") or "{}"),
                        }
                    )
                continue
            text = self._message_content_text(item)
            if text:
                items.append({"role": "user", "content": text})
        return items

    def _responses_tools(self, tool_schemas: Optional[List[Dict[str, object]]]) -> List[Dict[str, object]]:
        tools = []
        for schema in list(tool_schemas or []):
            if not isinstance(schema, dict):
                continue
            name = str(schema.get("name") or "").strip()
            if not name:
                continue
            tools.append(
                {
                    "type": "function",
                    "name": name,
                    "description": str(schema.get("description") or ""),
                    "parameters": dict(schema.get("parameters") or {"type": "object", "properties": {}}),
                }
            )
        return tools

    def _build_payload(
        self,
        *,
        system_prompt: str,
        messages: List[Dict[str, object]],
        tool_schemas: Optional[List[Dict[str, object]]] = None,
        stream: bool = False,
        text_format: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        payload: Dict[str, object] = {
            "model": self.model,
            "instructions": system_prompt,
            "input": self._responses_input(messages),
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        reasoning: Dict[str, object] = {}
        if self.reasoning_effort:
            reasoning["effort"] = self.reasoning_effort
        if self.reasoning_summary:
            reasoning["summary"] = self.reasoning_summary
        if reasoning:
            payload["reasoning"] = reasoning
        tools = self._responses_tools(tool_schemas)
        if tools:
            payload["tools"] = tools
        if text_format:
            payload["text"] = {"format": text_format}
        if stream:
            payload["stream"] = True
        return payload

    def _json_schema_text_format(self, *, schema_name: str, response_schema: Dict[str, object]) -> Dict[str, object]:
        return {
            "type": "json_schema",
            "name": schema_name,
            "schema": response_schema,
            "strict": True,
        }

    def _json_object_text_format(self) -> Dict[str, object]:
        return {"type": "json_object"}

    def _structured_format_attempt_order(self) -> List[str]:
        mode = str(self.structured_output_format or "json_schema").strip().lower()
        if mode == "json_object":
            return ["json_object", "json_schema", "prompt"]
        if mode == "prompt":
            return ["prompt"]
        return ["json_schema", "json_object", "prompt"]

    def _structured_text_format_for_name(self, *, schema_name: str, response_schema: Dict[str, object], format_name: str) -> Optional[Dict[str, object]]:
        if format_name == "json_schema":
            return self._json_schema_text_format(schema_name=schema_name, response_schema=response_schema)
        if format_name == "json_object":
            return self._json_object_text_format()
        if format_name == "prompt":
            return None
        return self._json_schema_text_format(schema_name=schema_name, response_schema=response_schema)

    def _initial_structured_text_format(self, *, schema_name: str, response_schema: Dict[str, object]) -> Optional[Dict[str, object]]:
        format_name = self._structured_format_attempt_order()[0]
        return self._structured_text_format_for_name(
            schema_name=schema_name,
            response_schema=response_schema,
            format_name=format_name,
        )

    def _set_successful_structured_output_format(self, format_name: str) -> None:
        normalized = str(format_name or "").strip().lower()
        if normalized not in {"json_schema", "json_object", "prompt"}:
            return
        self.structured_output_format = normalized
        callback = getattr(self, "structured_output_format_callback", None)
        if callback:
            callback(normalized)

    def _should_retry_with_json_object_text_format(self, exc: Exception) -> bool:
        lowered = _format_provider_exception(exc).lower()
        return (
            "text.format" in lowered
            or "response_format" in lowered
            or "json_schema" in lowered
            or "json_object" in lowered
            or "schema" in lowered
            or "strict" in lowered
            or "unknown parameter" in lowered
            or "invalid_request" in lowered
            or "unavailable" in lowered
            or "unsupported" in lowered
            or "not support" in lowered
            or "does not support" in lowered
        )

    def _should_retry_without_text_format(self, exc: Exception) -> bool:
        lowered = _format_provider_exception(exc).lower()
        return (
            "text.format" in lowered
            or "json_object" in lowered
            or "response_format" in lowered
            or "unknown parameter" in lowered
            or "invalid_request" in lowered
            or "unavailable" in lowered
            or "unsupported" in lowered
            or "not support" in lowered
            or "does not support" in lowered
        )

    def _make_request(self, payload: Dict[str, object], api_key: str) -> Request:
        return Request(
            self.base_url + "/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer %s" % api_key,
            },
            method="POST",
        )

    @staticmethod
    def _response_from_payload_static(parsed: Dict[str, object]) -> ProviderResponse:
        output_text = str(parsed.get("output_text") or "")
        content_parts: List[str] = [output_text] if output_text else []
        reasoning_parts: List[str] = []
        tool_calls: List[ProviderToolCall] = []
        for item in list(parsed.get("output") or []):
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "")
            if item_type == "message":
                for content_item in list(item.get("content") or []):
                    if not isinstance(content_item, dict):
                        continue
                    content_type = str(content_item.get("type") or "")
                    if content_type in {"output_text", "text", "input_text"} and content_item.get("text"):
                        text = str(content_item.get("text") or "")
                        if text and text not in content_parts:
                            content_parts.append(text)
            elif item_type == "function_call":
                tool_calls.append(
                    ProviderToolCall(
                        name=str(item.get("name") or ""),
                        arguments=_safe_parse_tool_arguments(str(item.get("arguments") or "{}")),
                        call_id=str(item.get("call_id") or item.get("id") or ""),
                    )
                )
            elif item_type == "reasoning":
                for summary_item in list(item.get("summary") or item.get("content") or []):
                    if isinstance(summary_item, dict) and summary_item.get("text"):
                        reasoning_parts.append(str(summary_item.get("text") or ""))
                    elif isinstance(summary_item, str):
                        reasoning_parts.append(summary_item)
                if item.get("text"):
                    reasoning_parts.append(str(item.get("text") or ""))
        return ProviderResponse(
            content="".join(content_parts),
            reasoning_content="".join(reasoning_parts),
            tool_calls=tool_calls,
            raw_payload=parsed,
        )

    def _response_from_payload(self, parsed: Dict[str, object]) -> ProviderResponse:
        return self._response_from_payload_static(parsed)

    def generate(self, *, system_prompt: str, messages: List[Dict[str, str]], tool_schemas: Optional[List[Dict[str, object]]] = None) -> ProviderResponse:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            return self._fallback("missing API key environment variable %s" % self.api_key_env).generate(
                system_prompt=system_prompt,
                messages=messages,
                tool_schemas=tool_schemas,
            )
        if Request is None or urlopen is None:
            return self._fallback("urllib request support is unavailable").generate(
                system_prompt=system_prompt,
                messages=messages,
                tool_schemas=tool_schemas,
            )
        payload = self._build_payload(system_prompt=system_prompt, messages=messages, tool_schemas=tool_schemas)
        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                request = self._make_request(payload, api_key)
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    parsed = json.loads(response.read().decode("utf-8"))
                return self._response_from_payload(parsed)
            except (HTTPError, URLError, KeyError, IndexError, ValueError) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff_seconds * float(attempt + 1))
                    continue
        return self._fallback("responses call failed: %s" % _format_provider_exception(last_exc)).generate(
            system_prompt=system_prompt,
            messages=messages,
            tool_schemas=tool_schemas,
        )

    def _iter_sse_events(self, response) -> Iterator[Dict[str, object]]:
        event_type = ""
        data_lines: List[str] = []
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                if data_lines:
                    data_text = "\n".join(data_lines).strip()
                    if data_text and data_text != "[DONE]":
                        try:
                            parsed = json.loads(data_text)
                            if event_type and not parsed.get("type"):
                                parsed["type"] = event_type
                            yield parsed
                        except ValueError:
                            pass
                event_type = ""
                data_lines = []
                continue
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            data_text = "\n".join(data_lines).strip()
            if data_text and data_text != "[DONE]":
                try:
                    parsed = json.loads(data_text)
                    if event_type and not parsed.get("type"):
                        parsed["type"] = event_type
                    yield parsed
                except ValueError:
                    pass

    def stream_generate(
        self,
        *,
        system_prompt: str,
        messages: List[Dict[str, str]],
        tool_schemas: Optional[List[Dict[str, object]]] = None,
    ) -> Iterator[ProviderStreamEvent]:
        if not self.stream:
            yield from super().stream_generate(system_prompt=system_prompt, messages=messages, tool_schemas=tool_schemas)
            return
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            yield from self._fallback("missing API key environment variable %s" % self.api_key_env).stream_generate(
                system_prompt=system_prompt,
                messages=messages,
                tool_schemas=tool_schemas,
            )
            return
        if Request is None or urlopen is None:
            yield from self._fallback("urllib request support is unavailable").stream_generate(
                system_prompt=system_prompt,
                messages=messages,
                tool_schemas=tool_schemas,
            )
            return
        payload = self._build_payload(system_prompt=system_prompt, messages=messages, tool_schemas=tool_schemas, stream=True)
        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                request = self._make_request(payload, api_key)
                builder = _OpenAIResponsesStreamBuilder()
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    for parsed in self._iter_sse_events(response):
                        event_type = str(parsed.get("type") or "")
                        if event_type in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}:
                            delta = str(parsed.get("delta") or "")
                            if delta.strip():
                                builder.add_reasoning(delta)
                                yield ProviderStreamEvent(type="reasoning_delta", text=delta, raw_payload=parsed)
                        elif event_type == "response.output_text.delta":
                            delta = str(parsed.get("delta") or "")
                            if delta:
                                builder.add_content(delta)
                                yield ProviderStreamEvent(type="text_delta", text=delta, raw_payload=parsed)
                        elif event_type in {"response.output_item.added", "response.output_item.done"}:
                            item = parsed.get("item") or parsed.get("output_item") or {}
                            if isinstance(item, dict):
                                builder.add_tool_item(parsed, item)
                        elif event_type == "response.function_call_arguments.delta":
                            builder.add_tool_arguments_delta(parsed)
                        elif event_type == "response.function_call_arguments.done":
                            builder.add_tool_arguments_done(parsed)
                        elif event_type in {"response.completed", "response.done"}:
                            response_payload = parsed.get("response") or parsed
                            if isinstance(response_payload, dict):
                                builder.completed_response = dict(response_payload)
                        elif event_type in {"response.failed", "error"}:
                            raise RuntimeError(json.dumps(parsed, ensure_ascii=False))
                response_payload = builder.to_response()
                yield ProviderStreamEvent(type="response", response=response_payload, raw_payload=response_payload.raw_payload)
                return
            except (HTTPError, URLError, KeyError, IndexError, ValueError, RuntimeError) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff_seconds * float(attempt + 1))
                    continue
        response = self.generate(system_prompt=system_prompt, messages=messages, tool_schemas=tool_schemas)
        if not (
            isinstance(response.content, str)
            and response.content.startswith("Moonshine processed the request in offline mode.")
        ):
            if str(response.reasoning_content or "").strip():
                yield ProviderStreamEvent(type="reasoning_delta", text=response.reasoning_content, raw_payload=response.raw_payload)
            if response.content:
                yield ProviderStreamEvent(type="text_delta", text=response.content, raw_payload=response.raw_payload)
            yield ProviderStreamEvent(type="response", response=response, raw_payload=response.raw_payload)
            return
        yield from self._fallback(
            "streaming responses call failed: %s" % _format_provider_exception(last_exc)
        ).stream_generate(system_prompt=system_prompt, messages=messages, tool_schemas=tool_schemas)

    def generate_structured(
        self,
        *,
        system_prompt: str,
        messages: List[Dict[str, str]],
        response_schema: Dict[str, object],
        schema_name: str,
    ) -> Dict[str, object]:
        prompt = (
            system_prompt
            + "\n\nJSON mode instructions:\n"
            + "- Return exactly one valid JSON object and no markdown fences or explanatory prose.\n"
            + "- The response must satisfy this JSON schema named `%s`:\n```json\n%s\n```"
            % (schema_name, json.dumps(response_schema, ensure_ascii=False, indent=2, sort_keys=True))
        )
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError("structured responses call failed: missing API key environment variable %s" % self.api_key_env)
        if Request is None or urlopen is None:
            raise RuntimeError("structured responses call failed: urllib request support is unavailable")

        format_order = self._structured_format_attempt_order()
        format_index = 0
        format_name = format_order[format_index]
        initial_format_name = format_name
        payload = self._build_payload(
            system_prompt=prompt,
            messages=messages,
            tool_schemas=None,
            text_format=self._structured_text_format_for_name(
                schema_name=schema_name,
                response_schema=response_schema,
                format_name=format_name,
            ),
        )
        attempts_remaining = self.max_retries
        last_exc = None
        while True:
            try:
                request = self._make_request(payload, api_key)
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    parsed_response = json.loads(response.read().decode("utf-8"))
                response = self._response_from_payload(parsed_response)
                parsed = _parse_json_object_from_text(response.content)
                result = _coerce_structured_payload(parsed, response_schema)
                if format_name != initial_format_name or self.structured_output_format not in {"auto", format_name}:
                    self._set_successful_structured_output_format(format_name)
                return result
            except (HTTPError, URLError, KeyError, IndexError, ValueError, JsonSchemaValidationError) as exc:
                last_exc = exc
                if (
                    self._should_retry_with_json_object_text_format(exc)
                    and format_index + 1 < len(format_order)
                ):
                    format_index += 1
                    format_name = format_order[format_index]
                    payload = self._build_payload(
                        system_prompt=prompt,
                        messages=messages,
                        tool_schemas=None,
                        text_format=self._structured_text_format_for_name(
                            schema_name=schema_name,
                            response_schema=response_schema,
                            format_name=format_name,
                        ),
                    )
                    if attempts_remaining <= 0:
                        attempts_remaining = 1
                    continue
                if attempts_remaining > 0:
                    attempts_remaining -= 1
                    time.sleep(self.retry_backoff_seconds * float(self.max_retries - attempts_remaining + 1))
                    continue
                break
        raise RuntimeError("structured responses call failed: %s" % _format_provider_exception(last_exc))


class AnthropicMessagesProvider(BaseProvider):
    """Placeholder Anthropic messages provider."""

    def __init__(self, model: str, api_key_env: str):
        self.model = model
        self.api_key_env = api_key_env

    def generate(self, *, system_prompt: str, messages: List[Dict[str, str]], tool_schemas: Optional[List[Dict[str, object]]] = None) -> ProviderResponse:
        return OfflineProvider(note="anthropic mode is scaffolded but not enabled in the offline test profile").generate(
            system_prompt=system_prompt,
            messages=messages,
            tool_schemas=tool_schemas,
        )


OpenAICompatibleProvider = OpenAIChatCompletionsProvider
