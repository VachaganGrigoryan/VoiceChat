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


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20)


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=20)


class MessageResponse(BaseModel):
    message: str
