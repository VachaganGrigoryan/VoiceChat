from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from starlette.requests import Request

from app.core.errors import AppError
from app.core.errors.openapi import build_error_responses
from app.core.http import SuccessResponse, ok
from app.core.security import require_verified_user
from app.db.models import (
    AuditLogDocument,
    ReportDocument,
    SlashCommandDocument,
    UserDocument,
    WebhookDocument,
)
from app.modules.extensibility.schemas import (
    AuditLogView,
    CreateWebhookRequest,
    RegisterSlashCommandRequest,
    ReportView,
    ResolveReportRequest,
    SlashCommandView,
    SubmitReportRequest,
    WebhookView,
)

extensibility_router = APIRouter()


# ==============================================================================
# Webhooks (Phase 8.2)
# ==============================================================================

@extensibility_router.post(
    "/webhooks",
    response_model=SuccessResponse[WebhookView],
    status_code=201,
    responses=build_error_responses(400, 401, 422, 500),
)
async def create_webhook(
    request: Request,
    body: CreateWebhookRequest,
    current_user: Annotated[UserDocument, Depends(require_verified_user)],
):
    # Model-seam only: create record, do not perform delivery execution
    webhook = WebhookDocument(
        direction=body.direction,
        target_type=body.target_type,
        target_id=body.target_id,
        url=body.url,
        created_by=current_user.str_id,
        events=body.events,
        active=True,
    )
    await webhook.insert()
    return ok(request, WebhookView.model_validate(webhook), status_code=201)


@extensibility_router.get(
    "/webhooks",
    response_model=SuccessResponse[list[WebhookView]],
    responses=build_error_responses(401, 422, 500),
)
async def list_webhooks(
    request: Request,
    current_user: Annotated[UserDocument, Depends(require_verified_user)],
    conversation_id: str | None = Query(default=None),
    space_id: str | None = Query(default=None),
):
    # Webhooks scoped to conversation or space
    query = {}
    if conversation_id:
        query = {"target_type": "conversation", "target_id": conversation_id}
    elif space_id:
        query = {"target_type": "space", "target_id": space_id}
    else:
        query = {"created_by": current_user.str_id}

    raw_webhooks = await WebhookDocument.find(query).to_list()
    return ok(request, [WebhookView.model_validate(w) for w in raw_webhooks])


# ==============================================================================
# Slash Commands (Phase 8.3)
# ==============================================================================

@extensibility_router.post(
    "/slash-commands",
    response_model=SuccessResponse[SlashCommandView],
    status_code=201,
    responses=build_error_responses(400, 401, 409, 422, 500),
)
async def register_slash_command(
    request: Request,
    body: RegisterSlashCommandRequest,
    current_user: Annotated[UserDocument, Depends(require_verified_user)],
):
    # Persist the slash command registration seam
    existing = await SlashCommandDocument.find_one(
        SlashCommandDocument.trigger == body.trigger
    )
    if existing:
        raise AppError(
            status_code=409,
            code="SLASH_COMMAND_ALREADY_EXISTS",
            message=f"Slash command with trigger /{body.trigger} is already registered.",
        )

    command = SlashCommandDocument(
        trigger=body.trigger,
        handler_url=body.handler_url,
        description=body.description,
        created_by=current_user.str_id,
        active=True,
    )
    await command.insert()
    return ok(request, SlashCommandView.model_validate(command), status_code=201)


@extensibility_router.get(
    "/slash-commands",
    response_model=SuccessResponse[list[SlashCommandView]],
    responses=build_error_responses(401, 422, 500),
)
async def list_slash_commands(
    request: Request,
    current_user: Annotated[UserDocument, Depends(require_verified_user)],
):
    commands = await SlashCommandDocument.find_all().to_list()
    return ok(request, [SlashCommandView.model_validate(c) for c in commands])


# ==============================================================================
# Moderation & Reports (Phase 8.4)
# ==============================================================================

@extensibility_router.post(
    "/reports",
    response_model=SuccessResponse[ReportView],
    status_code=201,
    responses=build_error_responses(400, 401, 422, 500),
)
async def submit_report(
    request: Request,
    body: SubmitReportRequest,
    current_user: Annotated[UserDocument, Depends(require_verified_user)],
):
    report = ReportDocument(
        target_type=body.target_type,
        target_id=body.target_id,
        reporter_id=current_user.str_id,
        reason=body.reason,
        status="pending",
    )
    await report.insert()
    return ok(request, ReportView.model_validate(report), status_code=201)


@extensibility_router.get(
    "/reports",
    response_model=SuccessResponse[list[ReportView]],
    responses=build_error_responses(401, 422, 500),
)
async def list_reports(
    request: Request,
    current_user: Annotated[UserDocument, Depends(require_verified_user)],
    status: Literal["pending", "resolved", "dismissed"] | None = Query(default=None),
):
    query = {}
    if status:
        query["status"] = status
    reports = await ReportDocument.find(query).to_list()
    return ok(request, [ReportView.model_validate(r) for r in reports])


@extensibility_router.patch(
    "/reports/{report_id}/resolve",
    response_model=SuccessResponse[ReportView],
    responses=build_error_responses(401, 404, 422, 500),
)
async def resolve_report(
    request: Request,
    report_id: str,
    body: ResolveReportRequest,
    current_user: Annotated[UserDocument, Depends(require_verified_user)],
):
    report = await ReportDocument.get(report_id)
    if not report:
        raise AppError(
            status_code=404,
            code="REPORT_NOT_FOUND",
            message="Report not found.",
        )

    report.status = body.status
    report.resolved_by = current_user.str_id
    report.resolved_at = datetime.now(UTC)
    await report.save()

    return ok(request, ReportView.model_validate(report))


# ==============================================================================
# Audit Logs (Phase 8.5)
# ==============================================================================

@extensibility_router.get(
    "/audit-logs",
    response_model=SuccessResponse[list[AuditLogView]],
    responses=build_error_responses(401, 422, 500),
)
async def list_audit_logs(
    request: Request,
    current_user: Annotated[UserDocument, Depends(require_verified_user)],
    space_id: str | None = Query(default=None),
):
    query = {}
    if space_id:
        query["space_id"] = space_id

    logs = await AuditLogDocument.find(query).to_list()
    return ok(request, [AuditLogView.model_validate(log) for log in logs])
