from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class StartAuthRequest(BaseModel):
    method: Literal["email"]
    identifier: EmailStr


class AuthChallengeResponse(BaseModel):
    method: Literal["email"]
    identifier: EmailStr
    message: str


class FinishAuthRequest(BaseModel):
    method: Literal["email"]
    identifier: EmailStr
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
