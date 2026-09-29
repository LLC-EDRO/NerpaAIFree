"""Validated OpenAI Structured Outputs boundary with bounded model fallback.

The API accepts a documented subset of JSON Schema.  This module makes that
contract executable before a paid request is sent and handles the response
states that do not contain a complete JSON document.
"""

from __future__ import annotations

import json
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from app.llm.usage import record_usage
from app.runtime.cancellation import check_cancelled

_UNSUPPORTED_KEYWORDS = {
    "allOf",
    "not",
    "dependentRequired",
    "dependentSchemas",
    "if",
    "then",
    "else",
    "patternProperties",
    "unevaluatedProperties",
    "propertyNames",
    "contains",
    "minContains",
    "maxContains",
    "uniqueItems",
    "minProperties",
    "maxProperties",
}
_SAFE_TO_PROJECT_OUT = {"default", "examples", "minLength", "maxLength"}


class StructuredOutputError(RuntimeError):
    """Base class for an observable Structured Outputs failure."""


class StructuredOutputSchemaError(StructuredOutputError):
    """Raised before the API call when the schema cannot be strict."""


class StructuredOutputRefusalError(StructuredOutputError):
    """The model explicitly refused the request; fallback is not attempted."""


class StructuredOutputIncompleteError(StructuredOutputError):
    """The response stopped before a complete structured value was produced."""

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.input_tokens = 0
        self.output_tokens = 0


class StructuredOutputResponseError(StructuredOutputError):
    """The provider returned empty or contract-invalid output."""


class StructuredOutputTransportError(StructuredOutputResponseError):
    """Every configured model failed because the provider transport was unavailable."""


def _is_transport_error(exc: BaseException) -> bool:
    """Recognize transient transport failures without importing an optional SDK."""

    current: BaseException | None = exc
    seen: set[int] = set()
    retryable_names = {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
    }
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (ConnectionError, TimeoutError)) or current.__class__.__name__ in retryable_names:
            return True
        current = current.__cause__ or current.__context__
    return False


@dataclass(slots=True)
class StructuredOutputCall[StructuredModel: BaseModel]:
    value: StructuredModel
    response: Any
    requested_model: str
    selected_model: str
    attempted_models: tuple[str, ...]

    @property
    def fallback_used(self) -> bool:
        return self.selected_model != self.requested_model


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Build and validate the strict schema projection for a Pydantic model."""

    schema = deepcopy(model.model_json_schema())

    def normalize(node: dict[str, Any], path: str) -> None:
        for keyword in _SAFE_TO_PROJECT_OUT:
            node.pop(keyword, None)
        if node.get("type") == "object":
            properties = node.get("properties")
            if not isinstance(properties, dict):
                raise StructuredOutputSchemaError(
                    f"{model.__name__}: {path} is a free-form object; use a typed object or key/value array"
                )
            node["additionalProperties"] = False
            node["required"] = list(properties)
        for container in ("properties", "$defs", "definitions"):
            children = node.get(container)
            if isinstance(children, dict):
                for key, value in children.items():
                    if isinstance(value, dict):
                        normalize(value, f"{path}.{container}.{key}")
        items = node.get("items")
        if isinstance(items, dict):
            normalize(items, f"{path}.items")
        for container in ("anyOf", "oneOf"):
            children = node.get(container)
            if isinstance(children, list):
                for index, value in enumerate(children):
                    if isinstance(value, dict):
                        normalize(value, f"{path}.{container}[{index}]")

    normalize(schema, "$")
    validate_strict_json_schema(schema, schema_name=model.__name__)
    return schema


def validate_strict_json_schema(schema: dict[str, Any], *, schema_name: str = "structured_output") -> None:
    """Validate OpenAI's strict JSON Schema subset without calling the API."""

    if schema.get("type") != "object" or "anyOf" in schema:
        raise StructuredOutputSchemaError(f"{schema_name}: the root schema must be an object, not anyOf")

    property_count = 0
    string_budget = 0
    enum_count = 0

    def visit(node: dict[str, Any], path: str, object_depth: int) -> None:
        nonlocal property_count, string_budget, enum_count
        unsupported = sorted(_UNSUPPORTED_KEYWORDS.intersection(node))
        if unsupported:
            raise StructuredOutputSchemaError(
                f"{schema_name}: {path} uses unsupported keyword(s): {', '.join(unsupported)}"
            )
        if node.get("type") == "object":
            object_depth += 1
            if object_depth > 10:
                raise StructuredOutputSchemaError(f"{schema_name}: object nesting exceeds 10 levels at {path}")
            properties = node.get("properties")
            if not isinstance(properties, dict):
                raise StructuredOutputSchemaError(f"{schema_name}: {path} must declare object properties")
            if node.get("additionalProperties") is not False:
                raise StructuredOutputSchemaError(f"{schema_name}: {path} must set additionalProperties=false")
            required = node.get("required")
            if required != list(properties):
                raise StructuredOutputSchemaError(f"{schema_name}: every property at {path} must be required")
            property_count += len(properties)
            string_budget += sum(len(str(key)) for key in properties)
        if node.get("type") == "array" and not isinstance(node.get("items"), dict):
            raise StructuredOutputSchemaError(f"{schema_name}: {path} arrays must declare one item schema")
        enum = node.get("enum")
        if isinstance(enum, list):
            enum_count += len(enum)
            string_budget += sum(len(str(value)) for value in enum)
            if len(enum) > 250 and sum(len(str(value)) for value in enum) > 15_000:
                raise StructuredOutputSchemaError(f"{schema_name}: enum string budget exceeds 15,000 at {path}")
        const = node.get("const")
        if const is not None:
            string_budget += len(str(const))
        for container in ("properties", "$defs", "definitions"):
            children = node.get(container)
            if isinstance(children, dict):
                for key, value in children.items():
                    if isinstance(value, dict):
                        visit(value, f"{path}.{container}.{key}", object_depth)
        items = node.get("items")
        if isinstance(items, dict):
            visit(items, f"{path}.items", object_depth)
        for container in ("anyOf", "oneOf"):
            children = node.get(container)
            if isinstance(children, list):
                for index, value in enumerate(children):
                    if isinstance(value, dict):
                        visit(value, f"{path}.{container}[{index}]", object_depth)

    visit(schema, "$", 0)
    if property_count > 5_000:
        raise StructuredOutputSchemaError(f"{schema_name}: schema contains {property_count} properties; maximum is 5000")
    if enum_count > 1_000:
        raise StructuredOutputSchemaError(f"{schema_name}: schema contains {enum_count} enum values; maximum is 1000")
    if string_budget > 120_000:
        raise StructuredOutputSchemaError(f"{schema_name}: schema string budget exceeds 120,000 characters")


def _refusal_text(response: Any) -> str | None:
    for output in getattr(response, "output", None) or []:
        for content in getattr(output, "content", None) or []:
            if getattr(content, "type", None) == "refusal":
                return str(getattr(content, "refusal", None) or "The model refused the request")
    return None


def _parse_response[StructuredModel: BaseModel](
    response: Any, model_type: type[StructuredModel]
) -> StructuredModel:
    refusal = _refusal_text(response)
    if refusal:
        raise StructuredOutputRefusalError(refusal)
    status = str(getattr(response, "status", "completed") or "completed")
    if status == "incomplete":
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", None) or "unknown"
        error = StructuredOutputIncompleteError(f"Structured response is incomplete: {reason}", reason=reason)
        usage = getattr(response, "usage", None)
        error.input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        error.output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        raise error
    if status not in {"completed", "success"}:
        raise StructuredOutputResponseError(f"Unexpected provider response status: {status}")
    output_text = getattr(response, "output_text", None)
    if not isinstance(output_text, str) or not output_text.strip():
        raise StructuredOutputResponseError("Provider response contains no structured output text")
    try:
        return model_type.model_validate_json(output_text)
    except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise StructuredOutputResponseError(
            f"Provider output does not satisfy {model_type.__name__}: {exc}"
        ) from exc


def compact_json_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove only JSON formatting whitespace, never fields or source text."""
    def compact(text: Any) -> Any:
        if not isinstance(text, str) or not text.lstrip().startswith(("{", "[")):
            return text
        try:
            json.loads(text)
            # Keep original numeric spellings and duplicate-key documents intact.
            def outside_strings(raw: str) -> str:
                result = []
                quoted = escaped = False
                for char in raw:
                    if quoted or not char.isspace():
                        result.append(char)
                    if escaped:
                        escaped = False
                    elif quoted and char == "\\":
                        escaped = True
                    elif char == '"':
                        quoted = not quoted
                return "".join(result)
            return outside_strings(text)
        except (ValueError, TypeError):
            return text
    result = deepcopy(messages)
    for message in result:
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = compact(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "input_text":
                    part["text"] = compact(part.get("text"))
    return result


def call_structured_output[StructuredModel: BaseModel](
    *,
    client: Any,
    model: str,
    fallback_models: tuple[str, ...] | list[str] = (),
    model_type: type[StructuredModel],
    schema_name: str,
    instructions: str,
    input: list[dict[str, Any]],
    max_output_tokens: int,
    prompt_cache_key: str,
    reasoning: dict[str, Any] | None = None,
    timeout_seconds: float | None = None,
    stop_on_incomplete: bool = False,
) -> StructuredOutputCall[StructuredModel]:
    """Call Responses using a prevalidated schema and one total timeout budget."""

    check_cancelled()
    schema = strict_json_schema(model_type)
    input = compact_json_input(input)
    models = tuple(dict.fromkeys(item for item in (model, *fallback_models) if item))
    attempted: list[str] = []
    last_error: Exception | None = None
    failures: list[Exception] = []
    started = time.monotonic()
    for model_index, selected_model in enumerate(models):
        remaining_timeout = None
        if timeout_seconds is not None:
            remaining_timeout = float(timeout_seconds) - (time.monotonic() - started)
            if remaining_timeout <= 0:
                break
        attempted.append(selected_model)
        try:
            kwargs: dict[str, Any] = {
                "model": selected_model,
                "instructions": instructions,
                "input": input,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    }
                },
                "max_output_tokens": max_output_tokens,
                "prompt_cache_key": prompt_cache_key[:64],
                "store": False,
            }
            if reasoning is not None:
                kwargs["reasoning"] = reasoning
            if remaining_timeout is not None:
                # OpenAI accepts a per-request timeout override. Every fallback
                # shares the same wall-clock budget. Reserve an equal share for
                # each model still in the chain so a slow primary cannot consume
                # the entire budget before the fallback is attempted.
                models_remaining = len(models) - model_index
                kwargs["timeout"] = max(0.1, remaining_timeout / models_remaining)
            try:
                check_cancelled()
                response = client.responses.create(**kwargs)
            except Exception:
                record_usage(None, model=selected_model, operation=schema_name)
                raise
            record_usage(response, model=selected_model, operation=schema_name)
            check_cancelled()
            parsed = _parse_response(response, model_type)
            return StructuredOutputCall(
                value=parsed,
                response=response,
                requested_model=model,
                selected_model=selected_model,
                attempted_models=tuple(attempted),
            )
        except StructuredOutputIncompleteError as exc:
            if stop_on_incomplete:
                raise
            last_error = exc
            failures.append(exc)
        except (StructuredOutputSchemaError, StructuredOutputRefusalError):
            raise
        except Exception as exc:  # provider/transport/invalid-output fallback is deliberate and bounded
            last_error = exc
            failures.append(exc)
    attempted_summary = ", ".join(attempted) or "none"
    if failures and all(_is_transport_error(item) for item in failures):
        raise StructuredOutputTransportError(
            f"Structured output transport failed for all configured models ({attempted_summary}): {last_error}"
        ) from last_error
    raise StructuredOutputResponseError(
        f"Structured output failed for all configured models ({attempted_summary}): {last_error}"
    ) from last_error
