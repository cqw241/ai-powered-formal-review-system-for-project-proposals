"""Policy upload, preview, candidate extraction, and draft editing."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings, get_settings
from app.db import get_db
from app.models import PolicyCandidate, PolicyDocument, PolicyStatus, utc_now
from app.schemas import (
    PolicyCandidateRead,
    PolicyCandidateUpdate,
    PolicyPageTextResponse,
    PolicyRead,
    PolicySummary,
)
from app.services import pdf as pdf_service
from app.services.money import UNIT_LABELS, parse_amount_text
from app.services.policy_extract import extract_candidates_from_pdf
from app.services.policy_storage import (
    looks_like_pdf,
    policy_file_path,
    relative_storage_key,
)

router = APIRouter(tags=["policies"])

ALLOWED_EXTENSIONS = {".pdf"}


def _safe_original_filename(filename: str | None) -> str:
    raw = (filename or "policy.pdf").strip() or "policy.pdf"
    name = raw.replace("\\", "/").split("/")[-1].strip() or "policy.pdf"
    while name.startswith(".."):
        name = name[2:].lstrip(".")
    name = name.replace("..", "_")
    if not name or name in {".", ".."}:
        name = "policy.pdf"
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    if len(name) > 255:
        stem = Path(name).stem[:240]
        name = f"{stem}.pdf"
    return name


def _get_policy_or_404(db: Session, policy_id: str) -> PolicyDocument:
    policy = db.get(PolicyDocument, policy_id)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="政策不存在")
    return policy


def _get_policy_with_candidates_or_404(db: Session, policy_id: str) -> PolicyDocument:
    statement = (
        select(PolicyDocument)
        .where(PolicyDocument.id == policy_id)
        .options(selectinload(PolicyDocument.candidates))
    )
    policy = db.scalars(statement).first()
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="政策不存在")
    return policy


def _ready_file_path(policy: PolicyDocument, settings: Settings) -> Path:
    if policy.status == PolicyStatus.FAILED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=policy.error_summary or "政策解析失败，无法读取页面",
        )
    if policy.status != PolicyStatus.READY.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"政策尚未就绪（当前状态：{policy.status}）",
        )
    try:
        path = policy_file_path(policy.id, settings)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="政策文件丢失，无法读取页面",
        )
    return path


def _guess_title_from_text(text: str, fallback: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        if "申报指南" in cleaned or "培育计划" in cleaned:
            return cleaned[:300]
    return fallback[:300]


def _apply_amount_raw(candidate: PolicyCandidate, amount_raw: str | None) -> None:
    """Normalize amount_raw via money service; unknown stays None (never 0)."""
    candidate.amount_raw = amount_raw
    if amount_raw is None:
        candidate.amount_yuan = None
        candidate.amount_unit = None
        return
    compact = amount_raw.replace(" ", "").replace("\u3000", "")
    parsed = parse_amount_text(amount_raw) or parse_amount_text(compact)
    if parsed is None:
        candidate.amount_yuan = None
        candidate.amount_unit = None
        return
    candidate.amount_yuan = parsed.amount_yuan
    candidate.amount_unit = UNIT_LABELS.get(parsed.unit)


@router.post(
    "/api/policies",
    response_model=PolicyRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_policy(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PolicyDocument:
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

    policy_id = str(uuid.uuid4())
    storage_key = relative_storage_key(policy_id)
    dest = policy_file_path(policy_id, settings)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)

    policy = PolicyDocument(
        id=policy_id,
        original_filename=original_filename,
        title=None,
        page_count=None,
        status=PolicyStatus.PROCESSING.value,
        error_summary=None,
        storage_path=storage_key,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)

    try:
        page_count = pdf_service.inspect_pdf(dest)
        first_page_text = pdf_service.get_page_text(dest, 1) if page_count >= 1 else ""
        extracted = extract_candidates_from_pdf(dest)
    except pdf_service.PdfError as exc:
        policy.status = PolicyStatus.FAILED.value
        policy.error_summary = exc.message
        policy.page_count = None
        db.add(policy)
        db.commit()
        return _get_policy_with_candidates_or_404(db, policy_id)

    policy.status = PolicyStatus.READY.value
    policy.page_count = page_count
    policy.error_summary = None
    policy.title = _guess_title_from_text(first_page_text, Path(original_filename).stem)
    db.add(policy)

    for item in extracted:
        db.add(
            PolicyCandidate(
                id=str(uuid.uuid4()),
                policy_id=policy_id,
                kind=item.kind,
                title=item.title,
                category=item.category,
                amount_raw=item.amount_raw,
                amount_yuan=item.amount_yuan,
                amount_unit=item.amount_unit,
                comparator=item.comparator,
                source_clause=item.source_clause,
                source_page=item.source_page,
                source_quote=item.source_quote,
                sort_order=item.sort_order,
            )
        )
    db.commit()
    return _get_policy_with_candidates_or_404(db, policy_id)


@router.get("/api/policies", response_model=list[PolicySummary])
def list_policies(db: Session = Depends(get_db)) -> list[PolicySummary]:
    count_sq = (
        select(PolicyCandidate.policy_id, func.count(PolicyCandidate.id).label("candidate_count"))
        .group_by(PolicyCandidate.policy_id)
        .subquery()
    )
    statement = (
        select(PolicyDocument, func.coalesce(count_sq.c.candidate_count, 0))
        .outerjoin(count_sq, PolicyDocument.id == count_sq.c.policy_id)
        .order_by(PolicyDocument.created_at.desc())
    )
    rows = db.execute(statement).all()
    results: list[PolicySummary] = []
    for policy, candidate_count in rows:
        results.append(
            PolicySummary(
                id=policy.id,
                original_filename=policy.original_filename,
                title=policy.title,
                page_count=policy.page_count,
                status=PolicyStatus(policy.status),
                error_summary=policy.error_summary,
                created_at=policy.created_at,
                candidate_count=int(candidate_count),
            )
        )
    return results


@router.get("/api/policies/{policy_id}", response_model=PolicyRead)
def get_policy(policy_id: str, db: Session = Depends(get_db)) -> PolicyDocument:
    return _get_policy_with_candidates_or_404(db, policy_id)


@router.get(
    "/api/policies/{policy_id}/pages/{page_number}/text",
    response_model=PolicyPageTextResponse,
)
def get_policy_page_text(
    policy_id: str,
    page_number: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PolicyPageTextResponse:
    policy = _get_policy_or_404(db, policy_id)
    path = _ready_file_path(policy, settings)
    try:
        text = pdf_service.get_page_text(path, page_number)
    except pdf_service.PdfError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc
    return PolicyPageTextResponse(
        policy_id=policy.id,
        page_number=page_number,
        page_count=policy.page_count or 0,
        text=text,
    )


@router.get("/api/policies/{policy_id}/pages/{page_number}/image")
def get_policy_page_image(
    policy_id: str,
    page_number: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    policy = _get_policy_or_404(db, policy_id)
    path = _ready_file_path(policy, settings)
    try:
        png = pdf_service.get_page_png(path, page_number)
    except pdf_service.PdfError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc
    return Response(content=png, media_type="image/png")


@router.patch(
    "/api/policies/{policy_id}/candidates/{candidate_id}",
    response_model=PolicyCandidateRead,
)
def update_policy_candidate(
    policy_id: str,
    candidate_id: str,
    body: PolicyCandidateUpdate,
    db: Session = Depends(get_db),
) -> PolicyCandidate:
    _get_policy_or_404(db, policy_id)
    candidate = db.get(PolicyCandidate, candidate_id)
    if candidate is None or candidate.policy_id != policy_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="候选要求不存在")

    data = body.model_dump(exclude_unset=True)
    if "title" in data and data["title"] is not None:
        candidate.title = data["title"]
    if "category" in data:
        candidate.category = data["category"]
    if "comparator" in data:
        candidate.comparator = data["comparator"]
    if "source_clause" in data:
        candidate.source_clause = data["source_clause"]
    if "source_page" in data:
        candidate.source_page = data["source_page"]
    if "source_quote" in data:
        candidate.source_quote = data["source_quote"]
    if "amount_raw" in data:
        _apply_amount_raw(candidate, data["amount_raw"])

    candidate.updated_at = utc_now()
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate
