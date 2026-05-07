"""Tool discovery and dispatch for Moonshine."""

from __future__ import annotations

import traceback
from typing import Dict, List, Optional, Sequence

def collect_tool_schemas(
    registry,
    mode: Optional[str] = None,
    *,
    include: Optional[Sequence[str]] = None,
    exclude: Optional[Sequence[str]] = None,
) -> List[Dict[str, object]]:
    """Return provider-facing tool schemas."""
    return registry.schemas(mode=mode, include=include, exclude=exclude)


def handle_function_calls(registry, calls: List[object], runtime: Dict[str, object]) -> List[Dict[str, object]]:
    """Dispatch provider tool calls through the registry."""
    results = []
    for call in calls:
        try:
            result = registry.dispatch(call.name, call.arguments, runtime)
            error = None
        except Exception as exc:
            result = {
                "error": str(exc),
                "traceback": traceback.format_exc(limit=3),
            }
            error = str(exc)
        results.append(
            {
                "name": call.name,
                "call_id": getattr(call, "call_id", ""),
                "arguments": call.arguments,
                "output": result,
                "error": error,
            }
        )
        runtime.setdefault("_tool_results_in_round", []).append(results[-1])
    return results
