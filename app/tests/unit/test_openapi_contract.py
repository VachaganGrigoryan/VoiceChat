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


def test_messages_media_upload_openapi_exposes_new_type_contract():
    spec = create_app().openapi()

    request_schema = spec["components"]["schemas"][
        "Body_upload_media_messages_media_post"
    ]

    assert request_schema["properties"]["type"]["enum"] == ["media", "file"]
    assert request_schema["properties"]["media_kind"]["anyOf"][0]["enum"] == [
        "voice",
        "audio",
        "image",
        "video",
    ]


def test_message_schemas_expose_normalized_message_and_media_kinds():
    spec = create_app().openapi()

    message_doc = spec["components"]["schemas"]["MessageDoc"]
    media_meta = spec["components"]["schemas"]["MediaMeta"]
    reply_preview = spec["components"]["schemas"]["ReplyPreview"]

    assert message_doc["properties"]["type"]["enum"] == ["text", "media", "file"]
    assert media_meta["properties"]["kind"]["enum"] == [
        "voice",
        "audio",
        "image",
        "video",
        "file",
    ]
    assert reply_preview["properties"]["type"]["enum"] == ["text", "media", "file"]
    assert reply_preview["properties"]["media_kind"]["anyOf"][0]["enum"] == [
        "voice",
        "audio",
        "image",
        "video",
        "file",
    ]
