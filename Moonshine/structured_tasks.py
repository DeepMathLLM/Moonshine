"""Central registry for structured tasks and their JSON schemas."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class StructuredTaskDefinition:
    """Describe one structured task that expects schema-constrained JSON output."""

    task_name: str
    schema_name: str
    schema: Dict[str, object]
    description: str = ""


_STRUCTURED_TASKS: Dict[str, StructuredTaskDefinition] = {}


def register_structured_task(
    *,
    task_name: str,
    schema_name: str,
    schema: Dict[str, object],
    description: str = "",
) -> StructuredTaskDefinition:
    """Register a structured task definition."""
    definition = StructuredTaskDefinition(
        task_name=str(task_name).strip(),
        schema_name=str(schema_name).strip(),
        schema=copy.deepcopy(dict(schema)),
        description=str(description).strip(),
    )
    _STRUCTURED_TASKS[definition.task_name] = definition
    return definition


def get_structured_task(task_name: str) -> StructuredTaskDefinition:
    """Return a structured task definition by task name."""
    normalized = str(task_name).strip()
    if normalized not in _STRUCTURED_TASKS:
        raise KeyError("structured task not found: %s" % normalized)
    return StructuredTaskDefinition(
        task_name=_STRUCTURED_TASKS[normalized].task_name,
        schema_name=_STRUCTURED_TASKS[normalized].schema_name,
        schema=copy.deepcopy(_STRUCTURED_TASKS[normalized].schema),
        description=_STRUCTURED_TASKS[normalized].description,
    )


def list_structured_tasks() -> List[StructuredTaskDefinition]:
    """Return all structured task definitions."""
    return [get_structured_task(name) for name in sorted(_STRUCTURED_TASKS)]

