"""Pydantic request/response schemas."""
from __future__ import annotations
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    tenant_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: "UserOut"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: EmailStr
    role: str
    is_active: bool
    created_at: datetime


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime


class MeResponse(BaseModel):
    user: UserOut
    tenant: TenantOut


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    tenant_id: uuid.UUID
    original_filename: str
    content_type: str
    size_bytes: int
    status: str
    scan_result: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class ColumnProfileOut(BaseModel):
    name: str
    type: str
    null_count: int
    non_null_count: int | None = None
    cardinality: int | None = None
    stats: dict[str, Any] = {}
    top_values: list[dict[str, Any]] = []


class DatasetProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    dataset_id: uuid.UUID
    row_count: int
    column_count: int
    columns: list[ColumnProfileOut]
    profile_meta: dict[str, Any]
    created_at: datetime


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    family_id: uuid.UUID
    ip: str | None = None
    user_agent: str | None = None
    created_at: datetime
    expires_at: datetime


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ApiKeyCreated(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    plaintext_key: str  # returned once, never again
    created_at: datetime


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    prefix: str
    last_used_at: datetime | None = None
    revoked: bool
    created_at: datetime


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    event_type: str
    resource_type: str | None = None
    resource_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    metadata_json: dict = Field(default_factory=dict, alias="metadata")
    created_at: datetime


LoginResponse.model_rebuild()
