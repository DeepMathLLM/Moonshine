"""Minimal JSON Schema validation helpers for Moonshine."""

from __future__ import annotations

import re
from typing import Any, Dict, List


class JsonSchemaValidationError(ValueError):
    """Raised when a payload does not satisfy a JSON schema."""


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def validate_json_schema(data: Any, schema: Dict[str, Any], path: str = "$") -> None:
    """Validate a value against a small JSON-schema subset."""
    expected_type = schema.get("type")
    if expected_type:
        if isinstance(expected_type, list):
            if not any(_matches_type(data, item) for item in expected_type):
                raise JsonSchemaValidationError("%s expected one of %s, got %s" % (path, expected_type, _type_name(data)))
        elif not _matches_type(data, str(expected_type)):
            raise JsonSchemaValidationError("%s expected %s, got %s" % (path, expected_type, _type_name(data)))

    if "enum" in schema and data not in schema["enum"]:
        raise JsonSchemaValidationError("%s must be one of %s" % (path, schema["enum"]))

    if isinstance(data, str):
        if "minLength" in schema and len(data) < int(schema["minLength"]):
            raise JsonSchemaValidationError("%s must have length >= %s" % (path, schema["minLength"]))
        if "maxLength" in schema and len(data) > int(schema["maxLength"]):
            raise JsonSchemaValidationError("%s must have length <= %s" % (path, schema["maxLength"]))
        if schema.get("pattern") and re.match(str(schema["pattern"]), data) is None:
            raise JsonSchemaValidationError("%s does not match pattern %s" % (path, schema["pattern"]))

    if isinstance(data, list):
        if "minItems" in schema and len(data) < int(schema["minItems"]):
            raise JsonSchemaValidationError("%s must contain at least %s items" % (path, schema["minItems"]))
        if "maxItems" in schema and len(data) > int(schema["maxItems"]):
            raise JsonSchemaValidationError("%s must contain at most %s items" % (path, schema["maxItems"]))
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(data):
                validate_json_schema(item, dict(item_schema), "%s[%s]" % (path, index))

    if isinstance(data, dict):
        required = list(schema.get("required") or [])
        for key in required:
            if key not in data:
                raise JsonSchemaValidationError("%s missing required property '%s'" % (path, key))
        properties = dict(schema.get("properties") or {})
        additional_allowed = schema.get("additionalProperties", True)
        for key, value in data.items():
            if key in properties:
                validate_json_schema(value, dict(properties[key]), "%s.%s" % (path, key))
            elif not additional_allowed:
                raise JsonSchemaValidationError("%s contains unexpected property '%s'" % (path, key))


def format_schema_for_prompt(schema: Dict[str, Any]) -> str:
    """Render a compact stable schema string for prompting."""
    import json

    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True)

