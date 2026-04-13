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

    call_meta = spec["components"]["schemas"]["CallMeta"]
    message_doc = spec["components"]["schemas"]["MessageDoc"]
    media_meta = spec["components"]["schemas"]["MediaMeta"]
    reply_preview = spec["components"]["schemas"]["ReplyPreview"]
    conversation_last_message = spec["components"]["schemas"]["ConversationLastMessage"]

    assert message_doc["properties"]["type"]["enum"] == [
        "text",
        "media",
        "file",
        "call",
    ]
    assert media_meta["properties"]["kind"]["enum"] == [
        "voice",
        "audio",
        "image",
        "video",
        "file",
    ]
    assert reply_preview["properties"]["type"]["enum"] == [
        "text",
        "media",
        "file",
        "call",
    ]
    assert reply_preview["properties"]["media_kind"]["anyOf"][0]["enum"] == [
        "voice",
        "audio",
        "image",
        "video",
        "file",
    ]
    assert "call" in message_doc["properties"]
    assert "call" in conversation_last_message["properties"]
    assert call_meta["properties"]["status"]["enum"] == [
        "rejected",
        "cancelled",
        "expired",
        "ended",
    ]


def test_calls_history_route_is_documented_with_paginated_response():
    spec = create_app().openapi()

    route = spec["paths"]["/calls/history"]["get"]
    response = route["responses"]["200"]

    assert _schema_ref_name(_json_schema(response)).startswith("PaginatedResponse_")


def test_call_schemas_expose_participant_state_contract():
    spec = create_app().openapi()

    call_doc = spec["components"]["schemas"]["CallDoc"]
    participant_state = spec["components"]["schemas"]["CallParticipantState"]

    assert "participant_states" in call_doc["properties"]
    assert participant_state["properties"]["role"]["enum"] == ["caller", "callee"]
    assert participant_state["properties"]["join_state"]["enum"] == [
        "waiting",
        "joined",
        "disconnected",
    ]
