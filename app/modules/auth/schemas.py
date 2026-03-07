from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class GenericEmailRequest(BaseModel):
    email: EmailStr


class GenericCodeSentResponse(BaseModel):
    email: EmailStr
    message: str


class VerifyRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class VerifyResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
