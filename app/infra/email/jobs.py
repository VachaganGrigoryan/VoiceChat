from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr


class SendVerificationCodeJob(BaseModel):
    type: Literal["send_verification_code"] = "send_verification_code"
    email: EmailStr
    code: str