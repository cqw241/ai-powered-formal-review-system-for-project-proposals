"""Project material upload, list, and page read endpoints."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import Material, MaterialCategory, MaterialStatus, Project
from app.schemas import MaterialRead, PageTextResponse
from app.services import pdf as pdf_service
from app.services.storage import (
    looks_like_pdf,
    material_file_path,
    relative_storage_key,
)

router = APIRouter(tags=["materials"])

ALLOWED_EXTENSIONS = {".pdf"}


def _get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    return project


def _get_material_or_404(db: Session, material_id: str) -> Material:
    material = db.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="材料不存在")
    return material


def _parse_category(raw: str) -> MaterialCategory:
    try:
        return MaterialCategory(raw.strip().upper())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in MaterialCategory)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"不支持的材料类别：{raw}；可选值：{allowed}",
        ) from exc


def _safe_original_filename(filename: str | None) -> str:
    """Keep a display name only; never trust path segments from the client."""
    raw = (filename or "upload.pdf").strip() or "upload.pdf"
    # Normalize separators then take the final segment.
    name = raw.replace("\\", "/").split("/")[-1].strip() or "upload.pdf"
    # Drop leftover traversal tokens from the basename.
    while name.startswith(".."):
        name = name[2:].lstrip(".")
    name = name.replace("..", "_")
    if not name or name in {".", ".."}:
        name = "upload.pdf"
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    if len(name) > 255:
        stem = Path(name).stem[:240]
        name = f"{stem}.pdf"
    return name


@router.post(
    "/api/projects/{project_id}/materials",
    response_model=MaterialRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_material(
    project_id: str,
    category: str = Form(..., description="APPLICATION | BUDGET | COMMITMENT | OTHER"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Material:
    _get_project_or_404(db, project_id)
    material_category = _parse_category(category)
    original_filename = _safe_original_filename(file.filename)

    suffix = Path(original_filename).suffix.lower()
    content_type = (file.content_type or "").lower()
    if suffix not in ALLOWED_EXTENSIONS and "pdf" not in content_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"仅支持 PDF 文件，收到：{file.filename or '未知文件'}",
        )

    max_bytes = settings.upload_max_bytes
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"文件过大：上限 {max_bytes // (1024 * 1024)}MB",
            )
        chunks.append(chunk)
    payload = b"".join(chunks)

    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="上传文件为空")
    if not looks_like_pdf(payload[:16]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不是有效的 PDF 文件：{original_filename}",
        )

    material_id = str(uuid.uuid4())
    storage_key = relative_storage_key(material_id)
    dest = material_file_path(material_id, settings)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)

    material = Material(
        id=material_id,
        project_id=project_id,
        original_filename=original_filename,
        category=material_category.value,
        page_count=None,
        status=MaterialStatus.PROCESSING.value,
        error_summary=None,
        storage_path=storage_key,
    )
    db.add(material)
    db.commit()
    db.refresh(material)

    try:
        page_count = pdf_service.inspect_pdf(dest)
    except pdf_service.PdfError as exc:
        material.status = MaterialStatus.FAILED.value
        material.error_summary = exc.message
        material.page_count = None
        db.add(material)
        db.commit()
        db.refresh(material)
        return material

    material.status = MaterialStatus.READY.value
    material.page_count = page_count
    material.error_summary = None
    db.add(material)
    db.commit()
    db.refresh(material)
    return material


@router.get("/api/projects/{project_id}/materials", response_model=list[MaterialRead])
def list_materials(project_id: str, db: Session = Depends(get_db)) -> list[Material]:
    _get_project_or_404(db, project_id)
    statement = (
        select(Material)
        .where(Material.project_id == project_id)
        .order_by(Material.created_at.desc())
    )
    return list(db.scalars(statement).all())


@router.get(
    "/api/materials/{material_id}/pages/{page_number}/text",
    response_model=PageTextResponse,
)
def get_page_text(
    material_id: str,
    page_number: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PageTextResponse:
    material = _get_material_or_404(db, material_id)
    path = _ready_file_path(material, settings)
    try:
        text = pdf_service.get_page_text(path, page_number)
    except pdf_service.PdfError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc
    return PageTextResponse(
        material_id=material.id,
        page_number=page_number,
        page_count=material.page_count or 0,
        text=text,
    )


@router.get("/api/materials/{material_id}/pages/{page_number}/image")
def get_page_image(
    material_id: str,
    page_number: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    material = _get_material_or_404(db, material_id)
    path = _ready_file_path(material, settings)
    try:
        png = pdf_service.get_page_png(path, page_number)
    except pdf_service.PdfError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc
    return Response(content=png, media_type="image/png")


def _ready_file_path(material: Material, settings: Settings) -> Path:
    if material.status == MaterialStatus.FAILED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=material.error_summary or "材料解析失败，无法读取页面",
        )
    if material.status != MaterialStatus.READY.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"材料尚未就绪（当前状态：{material.status}）",
        )
    try:
        path = material_file_path(material.id, settings)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="材料文件丢失，无法读取页面",
        )
    return path
