"""
schema — minimal JSON-Schema validator (subset of draft-07).

Avoids the `jsonschema` dependency. Supports: type, properties, required,
additionalProperties, items, prefixItems, pattern, enum, minLength, minimum,
const. Anything else is allowed through.

Usage:
    from spec.schema import validate
    errors = validate(instance, schema)
    if errors: print(errors)
"""

from typing import Any, Iterable, List


class ValidationError:
    __slots__ = ("path", "message")

    def __init__(self, path: str, message: str):
        self.path = path
        self.message = message

    def __str__(self):
        return f"{self.path}: {self.message}" if self.path != "" else self.message


def _type_ok(value: Any, type_name: str) -> bool:
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "null":
        return value is None
    return True  # unknown types are permissive


def _pattern_ok(value: Any, pattern: str) -> bool:
    import re
    return isinstance(value, str) and re.search(pattern, value) is not None


def _enum_ok(value: Any, enum: list) -> bool:
    return value in enum


def _min_ok(value: Any, minimum) -> bool:
    try:
        return value >= minimum
    except Exception:
        return True


def _minlen_ok(value: Any, n: int) -> bool:
    return hasattr(value, "__len__") and len(value) >= n


def validate(instance: Any, schema: dict, path: str = "") -> List[ValidationError]:
    errors: List[ValidationError] = []

    if not isinstance(schema, dict):
        return errors

    # type
    t = schema.get("type")
    if t is not None:
        if isinstance(t, list):
            if not any(_type_ok(instance, tt) for tt in t):
                errors.append(ValidationError(path, f"expected type {'|'.join(t)}, got {type(instance).__name__}"))
                return errors  # can't continue meaningfully
        else:
            if not _type_ok(instance, t):
                errors.append(ValidationError(path, f"expected type {t}, got {type(instance).__name__}"))
                return errors

    if t in (None, "object") and isinstance(instance, dict):
        # required
        for req in schema.get("required", []):
            if req not in instance:
                errors.append(ValidationError(f"{path}.{req}", "missing required field"))
        # properties
        for k, sub in (schema.get("properties") or {}).items():
            if k in instance:
                errors.extend(validate(instance[k], sub, f"{path}.{k}"))
        # additionalProperties
        addl = schema.get("additionalProperties", True)
        if addl is False:
            allowed = set((schema.get("properties") or {}).keys())
            for k in instance:
                if k not in allowed:
                    errors.append(ValidationError(f"{path}.{k}", "additional property not allowed"))

    if t in (None, "array") and isinstance(instance, list):
        if "items" in schema and isinstance(schema["items"], dict):
            for i, item in enumerate(instance):
                errors.extend(validate(item, schema["items"], f"{path}[{i}]"))
        if "prefixItems" in schema and isinstance(schema["prefixItems"], list):
            for i, item_schema in enumerate(schema["prefixItems"]):
                if i < len(instance):
                    errors.extend(validate(instance[i], item_schema, f"{path}[{i}]"))

    if "pattern" in schema:
        if not _pattern_ok(instance, schema["pattern"]):
            errors.append(ValidationError(path, f"does not match pattern {schema['pattern']!r}"))

    if "enum" in schema:
        if not _enum_ok(instance, schema["enum"]):
            errors.append(ValidationError(path, f"value not in enum {schema['enum']!r}"))

    if "minimum" in schema:
        if not _min_ok(instance, schema["minimum"]):
            errors.append(ValidationError(path, f"value below minimum {schema['minimum']}"))

    if "minLength" in schema:
        if not _minlen_ok(instance, schema["minLength"]):
            errors.append(ValidationError(path, f"length below minLength {schema['minLength']}"))

    if "const" in schema:
        if instance != schema["const"]:
            errors.append(ValidationError(path, f"value {instance!r} != const {schema['const']!r}"))

    return errors