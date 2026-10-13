"""Shared D2 demo-profile guard."""

from fastapi import HTTPException, status

from app.core.config import get_settings


def require_d2_demo_profile() -> None:
    if not get_settings().d2_demo_routes_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
