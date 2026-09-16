"""API request/response schemas for project and material resources."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.models import MaterialCategory, MaterialStatus


class ProjectCreate(BaseModel):
    name: str = Field(..., description="项目名称")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("项目名称不能为空")
        if len(cleaned) > 200:
            raise ValueError("项目名称不能超过 200 个字符")
        return cleaned


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def ensure_utc(cls, value: datetime) -> datetime:
        # SQLite may round-trip timezone-aware values as naive; treat naive as UTC.
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class MaterialRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    original_filename: str
    category: MaterialCategory
    page_count: int | None
    status: MaterialStatus
    error_summary: str | None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def ensure_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class PageTextResponse(BaseModel):
    material_id: str
    page_number: int
    page_count: int
    text: str


class HealthResponse(BaseModel):
    status: str
    llm_configured: bool
