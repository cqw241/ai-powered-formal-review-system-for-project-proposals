"""Server-managed material file storage."""

from __future__ import annotations

from pathlib import Path

from app.config import Settings, get_settings

PDF_MAGIC = b"%PDF"


def materials_dir(settings: Settings | None = None) -> Path:
    cfg = settings or get_settings()
    path = cfg.resolve_materials_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def material_file_path(material_id: str, settings: Settings | None = None) -> Path:
    """Absolute path for a stored material; filename is the material id only."""
    base = materials_dir(settings).resolve()
    # Reject path traversal: only allow a bare id as filename stem.
    if not material_id or "/" in material_id or "\\" in material_id or ".." in material_id:
        raise ValueError("非法材料 ID")
    path = (base / f"{material_id}.pdf").resolve()
    if path.parent != base:
        raise ValueError("非法材料存储路径")
    return path


def looks_like_pdf(header: bytes) -> bool:
    return header.lstrip().startswith(PDF_MAGIC)


def relative_storage_key(material_id: str) -> str:
    """Stable relative key stored in the database."""
    return f"{material_id}.pdf"
