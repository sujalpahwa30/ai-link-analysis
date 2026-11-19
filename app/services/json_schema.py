"""Convert Pydantic models into the JSON Schema subset accepted by strict
OpenAI structured-output models."""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel

_UNSUPPORTED_KEYWORDS = {
    "title",
    "default",
    "examples",
    "format",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "pattern",
    "uniqueItems",
}


def make_strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, list):
            return [resolve(item) for item in node]
        if not isinstance(node, dict):
            return node

        if "$ref" in node:
            reference = node["$ref"]
            prefix = "#/$defs/"
            if not reference.startswith(prefix):
                raise ValueError(f"Unsupported JSON Schema reference: {reference}")
            name = reference[len(prefix) :]
            if name not in definitions:
                raise ValueError(f"Unknown JSON Schema definition: {name}")
            return resolve(copy.deepcopy(definitions[name]))

        cleaned: dict[str, Any] = {}
        for key, value in node.items():
            if key == "$defs":
                continue
            if key == "properties" and isinstance(value, dict):
                # Keys under "properties" are model field names, not schema
                # keywords. A field may legitimately be called "title",
                # "format" or "default" and must never be stripped.
                cleaned["properties"] = {
                    name: resolve(subschema) for name, subschema in value.items()
                }
                continue
            if key in _UNSUPPORTED_KEYWORDS:
                continue
            cleaned[key] = resolve(value)

        if cleaned.get("type") == "object":
            properties = cleaned.get("properties", {})
            cleaned["required"] = list(properties.keys())
            cleaned["additionalProperties"] = False
        return cleaned

    result = resolve(copy.deepcopy(schema))
    if not isinstance(result, dict):
        raise ValueError("Root JSON Schema must be an object.")
    return result
