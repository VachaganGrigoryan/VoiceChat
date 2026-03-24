from __future__ import annotations

from app.factory import create_app


def _json_schema(response: dict) -> dict | None:
    return response.get("content", {}).get("application/json", {}).get("schema")


def _schema_ref_name(schema: dict | None) -> str | None:
    if not schema:
        return None
    ref = schema.get("$ref")
    if not ref:
        return None
    return ref.rsplit("/", 1)[-1]


def test_all_json_success_responses_use_shared_envelopes():
    spec = create_app().openapi()
    mismatches: list[str] = []

    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            for status_code, response in operation.get("responses", {}).items():
                if not status_code.startswith("2"):
                    continue

                schema = _json_schema(response)
                if schema is None:
                    continue

                name = _schema_ref_name(schema)
                if name and (
                    name.startswith("SuccessResponse_")
                    or name.startswith("PaginatedResponse_")
                ):
                    continue

                mismatches.append(f"{method.upper()} {path} -> {status_code}")

    assert mismatches == []


def test_all_documented_422_responses_use_error_response():
    spec = create_app().openapi()
    mismatches: list[str] = []

    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            response = operation.get("responses", {}).get("422")
            if response is None:
                continue

            schema = _json_schema(response)
            if schema == {"$ref": "#/components/schemas/ErrorResponse"}:
                continue

            mismatches.append(f"{method.upper()} {path}")

    assert mismatches == []
