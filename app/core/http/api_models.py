from __future__ import annotations

from typing import Any, Generic, Optional, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")

class ErrorDetail(BaseModel):
    field: Optional[str] = None
    message: str
    type: Optional[str] = None


class ErrorEnvelope(BaseModel):
    code: str = Field(..., examples=["VALIDATION_ERROR", "UNAUTHORIZED", "INTERNAL_ERROR"])
    message: str
    details: Optional[Any] = None  # can be list[ErrorDetail] or dict


class ErrorResponse(BaseModel):
    success: bool = Field(default=False)
    error: ErrorEnvelope
    request_id: Optional[str] = None


class Meta(BaseModel):
    # Optional: for pagination, counts, etc.
    cursor: Optional[str] = None
    next_cursor: Optional[str] = None
    limit: Optional[int] = None
    total: Optional[int] = None


class SuccessResponse(BaseModel, Generic[T]):
    success: bool = Field(default=True)
    data: T
    meta: Optional[Meta] = None
    request_id: Optional[str] = None