"""Model metadata used for context-window budgeting."""

from __future__ import annotations

from typing import Dict, Optional


DEFAULT_CONTEXT_WINDOW_TOKENS = 258000


# Values are intentionally conservative public API context windows. Azure
# deployment names are user-defined, so prefix matching handles names such as
# `gpt-5-chat` that point at a GPT-5 deployment.
MODEL_CONTEXT_WINDOWS: Dict[str, int] = {
    "gpt-5": 400000,
    "gpt-5.1": 400000,
    "gpt-5.2": 400000,
    "gpt-5-mini": 400000,
    "gpt-5-nano": 400000,
    "gpt-4.1": 1047576,
    "gpt-4.1-mini": 1047576,
    "gpt-4.1-nano": 1047576,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "o1": 200000,
    "o1-mini": 128000,
    "o3": 200000,
    "o3-mini": 200000,
    "o4-mini": 200000,
}


def normalize_model_name(model: str) -> str:
    """Normalize a model or deployment name for metadata lookup."""
    return str(model or "").strip().lower().replace("_", "-")


def resolve_model_context_window(model: str, *, configured: Optional[int] = None) -> int:
    """Return the context window to use for a model.

    A positive configured value wins. A non-positive value is treated as the
    explicit "auto" sentinel: known model/deployment names are resolved by exact
    or prefix match, with a stable 258K fallback.
    """
    try:
        configured_value = int(configured or 0)
    except (TypeError, ValueError):
        configured_value = 0
    if configured_value > 0:
        return configured_value

    name = normalize_model_name(model)
    if not name:
        return DEFAULT_CONTEXT_WINDOW_TOKENS
    if name in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[name]

    for prefix in sorted(MODEL_CONTEXT_WINDOWS, key=len, reverse=True):
        if name.startswith(prefix + "-") or name.startswith(prefix + "."):
            return MODEL_CONTEXT_WINDOWS[prefix]
    return DEFAULT_CONTEXT_WINDOW_TOKENS
