from __future__ import annotations

from fastapi import APIRouter

# Legacy peer-id `/messages/*` endpoints are intentionally unavailable.
# Supported message operations live under `/conversations/{conversation_id}/messages`.
router = APIRouter(prefix="/messages", tags=["messages"], include_in_schema=False)
