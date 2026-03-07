from __future__ import annotations

from typing import Dict, Optional
from pydantic import BaseModel


class DependencyHealth(BaseModel):
    status: str
    error: Optional[str] = None
    provider: Optional[str] = None


class HealthChecks(BaseModel):
    mongo: Optional[DependencyHealth] = None
    redis: Optional[DependencyHealth] = None
    rabbitmq: Optional[DependencyHealth] = None
    storage: Optional[DependencyHealth] = None


class LiveStatus(BaseModel):
    status: str


class HealthStatus(BaseModel):
    status: str
    checks: Optional[Dict[str, DependencyHealth]] = None
