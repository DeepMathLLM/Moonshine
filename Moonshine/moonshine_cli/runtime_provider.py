"""Resolve Moonshine runtime providers from config."""

from __future__ import annotations

from moonshine.agent_runtime.model_metadata import resolve_model_context_window
from moonshine.moonshine_cli.config import AppConfig
from moonshine.providers import (
    AnthropicMessagesProvider,
    AzureOpenAIChatCompletionsProvider,
    BaseProvider,
    OfflineProvider,
    OpenAIChatCompletionsProvider,
    OpenAIResponsesProvider,
)


def _resolve_provider_settings(provider_config) -> BaseProvider:
    """Resolve a provider implementation from one provider-like config object."""
    provider_type = (provider_config.type or "offline").strip().lower()
    max_context_tokens = resolve_model_context_window(
        provider_config.model,
        configured=getattr(provider_config, "max_context_tokens", 0),
    )
    if provider_type in {"offline", "local"}:
        return OfflineProvider()
    if provider_type in {"openai_compatible", "openai_chat", "chat_completions"}:
        return OpenAIChatCompletionsProvider(
            model=provider_config.model,
            base_url=provider_config.base_url,
            api_key_env=provider_config.api_key_env,
            timeout_seconds=provider_config.timeout_seconds,
            temperature=provider_config.temperature,
            stream=provider_config.stream,
            max_retries=provider_config.max_retries,
            retry_backoff_seconds=provider_config.retry_backoff_seconds,
            max_context_tokens=max_context_tokens,
        )
    if provider_type in {"azure_openai", "azure", "azure_chat_completions"}:
        return AzureOpenAIChatCompletionsProvider(
            model=provider_config.model,
            base_url=provider_config.base_url,
            api_key_env=provider_config.api_key_env,
            api_version=provider_config.api_version or "2024-12-01-preview",
            timeout_seconds=provider_config.timeout_seconds,
            temperature=provider_config.temperature,
            stream=provider_config.stream,
            max_retries=provider_config.max_retries,
            retry_backoff_seconds=provider_config.retry_backoff_seconds,
            max_context_tokens=max_context_tokens,
        )
    if provider_type in {"openai_responses", "responses"}:
        return OpenAIResponsesProvider(
            model=provider_config.model,
            base_url=provider_config.base_url,
            api_key_env=provider_config.api_key_env,
        )
    if provider_type in {"anthropic", "anthropic_messages"}:
        return AnthropicMessagesProvider(
            model=provider_config.model,
            api_key_env=provider_config.api_key_env,
        )
    return OfflineProvider(note="unknown provider type '%s'" % provider_type)


def resolve_runtime_provider(config: AppConfig) -> BaseProvider:
    """Resolve the main runtime provider from app config."""
    return _resolve_provider_settings(config.provider)


def resolve_verification_provider(config: AppConfig, *, fallback_provider: BaseProvider) -> BaseProvider:
    """Resolve the dedicated verification provider or inherit the main provider."""
    verification = config.verification_provider
    if bool(getattr(verification, "inherit_from_main", True)):
        return fallback_provider
    return _resolve_provider_settings(verification)
