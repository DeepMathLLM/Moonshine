"""Provider implementations for Moonshine."""

from __future__ import annotations

import json
import os
import time
from urllib.parse import quote
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional

from moonshine.agent_runtime.model_metadata import DEFAULT_CONTEXT_WINDOW_TOKENS
from moonshine.json_schema import validate_json_schema

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
    raw_payload: Dict[str, object] = field(default_factory=dict)


@dataclass
class ProviderStreamEvent:
    """Incremental provider event."""

    type: str
    text: str = ""
    response: Optional[ProviderResponse] = None
    raw_payload: Dict[str, object] = field(default_factory=dict)


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
        validate_json_schema(parsed, response_schema)
        return dict(parsed)


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
        self.tool_call_parts: Dict[int, Dict[str, str]] = {}

    def add_content(self, text: str) -> None:
        """Append streamed text."""
        if text:
            self.content_parts.append(text)

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
        stream: bool = True,
        max_retries: int = 2,
        retry_backoff_seconds: float = 1.0,
        max_context_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.stream = stream
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self.max_context_tokens = max(1024, int(max_context_tokens))

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
        if tool_schemas:
            payload["tools"] = [{"type": "function", "function": schema} for schema in tool_schemas]
        if response_format:
            payload["response_format"] = response_format
        if stream:
            payload["stream"] = True
        return payload

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

        payload = self._build_payload(
            system_prompt=system_prompt,
            messages=messages,
            tool_schemas=[],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": response_schema,
                },
            },
        )
        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                request = self._make_request(payload, api_key)
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    parsed_payload = json.loads(response.read().decode("utf-8"))
                message = parsed_payload["choices"][0]["message"]
                parsed = json.loads((message.get("content", "") or "").strip())
                validate_json_schema(parsed, response_schema)
                return dict(parsed)
            except (HTTPError, URLError, KeyError, IndexError, ValueError) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff_seconds * float(attempt + 1))
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
        stream: bool = True,
        max_retries: int = 2,
        retry_backoff_seconds: float = 1.0,
        max_context_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS,
    ):
        super().__init__(
            model=model,
            base_url=base_url,
            api_key_env=api_key_env,
            timeout_seconds=timeout_seconds,
            temperature=temperature,
            stream=stream,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            max_context_tokens=max_context_tokens,
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

        payload = self._build_payload(
            system_prompt=system_prompt,
            messages=messages,
            tool_schemas=[],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": response_schema,
                },
            },
        )
        attempts_remaining = self.max_retries + 1
        retried_without_temperature = False
        last_exc = None
        while attempts_remaining > 0:
            try:
                request = self._make_request(payload, api_key)
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    parsed_payload = json.loads(response.read().decode("utf-8"))
                message = parsed_payload["choices"][0]["message"]
                parsed = json.loads((message.get("content", "") or "").strip())
                validate_json_schema(parsed, response_schema)
                return dict(parsed)
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


class OpenAIResponsesProvider(BaseProvider):
    """Placeholder OpenAI responses provider."""

    def __init__(self, model: str, base_url: str, api_key_env: str):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env

    def generate(self, *, system_prompt: str, messages: List[Dict[str, str]], tool_schemas: Optional[List[Dict[str, object]]] = None) -> ProviderResponse:
        return OfflineProvider(note="responses mode is scaffolded but not enabled in the offline test profile").generate(
            system_prompt=system_prompt,
            messages=messages,
            tool_schemas=tool_schemas,
        )


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
