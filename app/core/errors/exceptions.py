from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class AppError(Exception):
    """
    Use this everywhere in services. The global exception handler converts it to ErrorResponse.
    """
    code: str
    message: str
    status_code: int = 400
    details: Optional[Any] = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def not_found(message: str = "Not found", *, details: Any = None) -> AppError:
    return AppError(code="NOT_FOUND", message=message, status_code=404, details=details)

def unauthorized(message: str = "Unauthorized") -> AppError:
    return AppError(code="UNAUTHORIZED", message=message, status_code=401)

def forbidden(message: str = "Forbidden") -> AppError:
    return AppError(code="FORBIDDEN", message=message, status_code=403)