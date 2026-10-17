"""Export the thin Omnix gateway OpenAPI schema to disk."""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _stabilize_integral_json_numbers(value: object) -> object:
    """Canonicalize whole-valued floats so generated OpenAPI is byte-stable.

    Pydantic may emit numeric constraints such as 0 or 0.0 depending on the
    runtime path that constructed an equivalent core schema. JSON Schema
    treats those values identically, but Omnix checks the generated contract
    byte-for-byte. Normalize only exact whole-valued floats; non-integral
    values, booleans, strings, and all schema structure remain unchanged.
    """

    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        return [_stabilize_integral_json_numbers(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _stabilize_integral_json_numbers(item)
            for key, item in value.items()
        }
    return value

def _stabilize_equivalent_io_schemas(schema: dict[str, object]) -> None:
    """Preserve legacy input/output component identities when they are equivalent.

    FastAPI/Pydantic releases can deduplicate a model's validation and
    serialization schemas when their structures are identical. Omnix commits the
    generated gateway contract, so that implementation-level optimization would
    otherwise create unrelated contract churn even though the public schema did
    not change.

    AssistantToolsConfigPayload historically has explicit ``-Input`` and
    ``-Output`` components, and the two structures are intentionally identical.
    If the generator collapses them to one equivalent component, expand that
    component back to the stable checked-in identities. This changes names only;
    it never rewrites fields, requirements, types, or endpoint paths.
    """

    components = schema.get("components")
    if not isinstance(components, dict):
        return
    schemas = components.get("schemas")
    if not isinstance(schemas, dict):
        return

    canonical_name = "AssistantToolsConfigPayload"
    input_name = f"{canonical_name}-Input"
    output_name = f"{canonical_name}-Output"
    value = schemas.get(canonical_name)
    if (
        not isinstance(value, dict)
        or input_name in schemas
        or output_name in schemas
    ):
        return

    # Keep this normalization deliberately narrow. If future input/output
    # contracts genuinely diverge, FastAPI will emit distinct components and
    # this branch will not run.
    schemas[input_name] = copy.deepcopy(value)
    schemas[output_name] = copy.deepcopy(value)
    del schemas[canonical_name]

    canonical_ref = f"#/components/schemas/{canonical_name}"

    def rewrite(value: object, *, request: bool = False) -> None:
        if isinstance(value, list):
            for item in value:
                rewrite(item, request=request)
        elif isinstance(value, dict):
            if value.get("$ref") == canonical_ref:
                value["$ref"] = f"#/components/schemas/{input_name if request else output_name}"
            for key, item in value.items():
                rewrite(item, request=request or key == "requestBody")

    rewrite(schema)


_SCHEMA_REF = "#/components/schemas/"


def _referenced_schemas(value: object) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith(_SCHEMA_REF):
            names.add(reference[len(_SCHEMA_REF):])
        for item in value.values():
            names |= _referenced_schemas(item)
    elif isinstance(value, list):
        for item in value:
            names |= _referenced_schemas(item)
    return names


def _closure(roots: set[str], schemas: dict[str, object]) -> set[str]:
    seen: set[str] = set()
    pending = list(roots)
    while pending:
        name = pending.pop()
        if name in seen or name not in schemas:
            continue
        seen.add(name)
        pending.extend(_referenced_schemas(schemas[name]) - seen)
    return seen


def _rewrite_refs(value: object, names: set[str], suffix: str) -> None:
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith(_SCHEMA_REF) and reference[len(_SCHEMA_REF):] in names:
            value["$ref"] = f"{reference}{suffix}"
        for item in value.values():
            _rewrite_refs(item, names, suffix)
    elif isinstance(value, list):
        for item in value:
            _rewrite_refs(item, names, suffix)


def _defaulted_fields(name: str, component: object, factory_fields: dict[str, frozenset[str]]) -> list[str]:
    """Fields the server fills when a caller leaves them out.

    The document records most defaults, but not default factories, and FastAPI
    drops ``"default": null``; ``factory_fields`` carries those from the models.
    """
    properties = component.get("properties") if isinstance(component, dict) else None
    if not isinstance(properties, dict):
        return []
    factories = factory_fields.get(name.removesuffix("-Output"), frozenset())
    return [
        key for key, value in properties.items()
        if isinstance(value, dict) and ("default" in value or key in factories)
    ]


def _require_serialized_defaults(
    schema: dict[str, object],
    lenient_operations: frozenset[tuple[str, str]] = frozenset(),
    factory_fields: dict[str, frozenset[str]] | None = None,
) -> None:
    """Make the contract say which response fields are always present (WP-9.3).

    Pydantic omits fields with defaults from ``required``, which is right for
    request bodies (callers may leave them out) but not for responses: the
    gateway always serializes them. Clients generated from the contract would
    otherwise treat every defaulted response field as possibly missing.

    Schemas only responses use get their defaulted fields marked required.
    Schemas both requests and responses use are split the way FastAPI splits
    models whose input and output differ: ``Name`` describes the response and
    a ``Name-Input`` copy, referenced from request bodies and parameters, keeps
    Pydantic's request view. Responses of operations that drop unset, None or
    default fields (``lenient_operations``, as ``(METHOD, path)``) keep
    Pydantic's view too. ``factory_fields`` names, per component, the
    defaulted fields the document does not mark with ``default``.
    """

    factory_fields = factory_fields or {}
    components = schema.get("components")
    schemas = components.get("schemas") if isinstance(components, dict) else None
    paths = schema.get("paths")
    if not isinstance(schemas, dict) or not isinstance(paths, dict):
        return
    inputs: set[str] = set()
    outputs: set[str] = set()
    lenient: set[str] = set()
    request_parts: list[object] = []
    for path, operations in paths.items():
        if not isinstance(operations, dict):
            continue
        for method, operation in operations.items():
            if not isinstance(operation, dict):
                continue
            request_parts += [operation.get("requestBody"), operation.get("parameters")]
            inputs |= _referenced_schemas(operation.get("requestBody")) | _referenced_schemas(operation.get("parameters"))
            responses = _referenced_schemas(operation.get("responses"))
            outputs |= responses
            if (method.upper(), path) in lenient_operations:
                lenient |= responses
    input_closure = _closure(inputs, schemas)
    output_closure = _closure(outputs, schemas)
    lenient_closure = _closure(lenient, schemas)

    shared = {
        name for name in (input_closure & output_closure) - lenient_closure
        if not name.endswith(("-Input", "-Output")) and f"{name}-Input" not in schemas
    }
    # Split a shared schema when it, or a shared schema it references, has defaults.
    split = {name for name in shared if _defaulted_fields(name, schemas[name], factory_fields)}
    while True:
        more = {name for name in shared - split if _referenced_schemas(schemas[name]) & split}
        if not more:
            break
        split |= more
    for name in sorted(split):
        schemas[f"{name}-Input"] = copy.deepcopy(schemas[name])
    # Request copies and request-only schemas reference the request copies.
    for name in split:
        _rewrite_refs(schemas[f"{name}-Input"], split, "-Input")
    for name in input_closure - output_closure:
        _rewrite_refs(schemas[name], split, "-Input")
    for part in request_parts:
        _rewrite_refs(part, split, "-Input")

    for name in sorted((output_closure - input_closure - lenient_closure) | split):
        component = schemas[name]
        defaulted = _defaulted_fields(name, component, factory_fields)
        if not defaulted:
            continue
        required = list(component.get("required", []))
        component["required"] = required + [key for key in defaulted if key not in required]


def _model_default_fields(app: object) -> dict[str, frozenset[str]]:
    """Per model name, the fields with a default or default factory, from the models the routes use."""
    import typing

    from pydantic import BaseModel

    found: dict[str, set[str]] = {}
    seen: set[type] = set()

    def visit(annotation: object) -> None:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if annotation in seen:
                return
            seen.add(annotation)
            for field_name, field in annotation.model_fields.items():
                if not field.is_required():
                    key = field.serialization_alias or field.alias or field_name
                    found.setdefault(annotation.__name__, set()).add(key)
                visit(field.annotation)
            return
        for argument in typing.get_args(annotation):
            visit(argument)

    for _path, route in _api_routes(app):
        visit(route.response_model)
        body = getattr(route, "body_field", None)
        visit(getattr(body, "type_", None) or getattr(getattr(body, "field_info", None), "annotation", None))
    return {name: frozenset(fields) for name, fields in found.items()}


def normalize_contract(schema: dict[str, object], app: object) -> dict[str, object]:
    """The gateway's OpenAPI document as the web contract stores it."""
    _stabilize_equivalent_io_schemas(schema)
    _require_serialized_defaults(schema, _lenient_operations(app), _model_default_fields(app))
    return _stabilize_integral_json_numbers(schema)


def _api_routes(app: object) -> list[tuple[str, object]]:
    """Every API route with its full path; included routers stay nested in FastAPI >= 0.141."""
    from fastapi.routing import APIRoute, iter_route_contexts

    return [
        (context.path or context.original_route.path, context.original_route)
        for context in iter_route_contexts(getattr(app, "routes", []))
        if isinstance(context.original_route, APIRoute)
    ]


def _lenient_operations(app: object) -> frozenset[tuple[str, str]]:
    operations: set[tuple[str, str]] = set()
    for path, route in _api_routes(app):
        if (
            route.response_model_exclude_unset
            or route.response_model_exclude_none
            or route.response_model_exclude_defaults
        ):
            operations.update((method, path) for method in route.methods)
    return frozenset(operations)


KERNEL_OWNER = "kernel"
ROUTE_OWNERS_FILE = "route-owners.json"
_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})


def route_owners(schema: dict[str, object], owners: dict[tuple[str, str], str]) -> dict[str, str]:
    """Each documented operation ("METHOD path") and the feature that mounted it, else kernel (PA-2.4)."""
    # OpenAPI drops path converters: a route's `{instrument_id:path}` is documented as `{instrument_id}`.
    owners = {(method, re.sub(r"\{([^}:]+):[^}]*\}", r"{\1}", path)): owner for (method, path), owner in owners.items()}
    paths = schema.get("paths")
    result: dict[str, str] = {}
    for path, item in sorted(paths.items() if isinstance(paths, dict) else ()):
        for method in sorted(item):
            if method in _HTTP_METHODS:
                result[f"{method.upper()} {path}"] = owners.get((method.upper(), path), KERNEL_OWNER)
    return result


def _gateway_app() -> object:
    src_dir = _repo_root() / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    from app.composition.gateway.main import create_gateway_app

    return create_gateway_app()


def export_contract() -> tuple[dict[str, object], dict[str, str]]:
    """The gateway OpenAPI document as the web contract stores it, and its route owners."""
    app = _gateway_app()
    schema = normalize_contract(app.openapi(), app)
    return schema, route_owners(schema, dict(getattr(app.state, "route_owners", {}) or {}))


def export_schema() -> dict[str, object]:
    """Return the gateway OpenAPI document exactly as the web contract stores it."""
    return export_contract()[0]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python scripts/export_gateway_openapi.py <output-json>", file=sys.stderr)
        return 2

    output_path = (_repo_root() / argv[1]).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    schema, owners = export_contract()
    output_path.write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    # Route ownership lives beside the document, which stays unchanged (PA-2.4).
    (output_path.parent / ROUTE_OWNERS_FILE).write_text(
        json.dumps({"operations": owners}, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
