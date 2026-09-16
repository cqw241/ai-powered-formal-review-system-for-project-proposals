"""Server-managed policy PDF storage (separate from project materials)."""

from __future__ import annotations

from pathlib import Path

from app.config import Settings, get_settings

PDF_MAGIC = b"%PDF"


def policies_dir(settings: Settings | None = None) -> Path:
    cfg = settings or get_settings()
    path = cfg.resolve_policies_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def policy_file_path(policy_id: str, settings: Settings | None = None) -> Path:
    """Absolute path for a stored policy; filename is the policy id only."""
    base = policies_dir(settings).resolve()
    if not policy_id or "/" in policy_id or "\\" in policy_id or ".." in policy_id:
        raise ValueError("非法政策 ID")
    path = (base / f"{policy_id}.pdf").resolve()
    if path.parent != base:
        raise ValueError("非法政策存储路径")
    return path


def looks_like_pdf(header: bytes) -> bool:
    return header.lstrip().startswith(PDF_MAGIC)


def relative_storage_key(policy_id: str) -> str:
    """Stable relative key stored in the database."""
    return f"{policy_id}.pdf"
