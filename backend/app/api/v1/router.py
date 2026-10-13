"""API v1 router registry."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, require_roles
from app.api.v1 import (
    assist,
    auth,
    dashboard,
    enterprise_access,
    fairs,
    imports,
    jobs,
    llm_batches,
    profiles,
    reports,
    scene,
    student_access,
    visits,
)
from app.core.config import settings
from app.core.rbac import Role
from app.d2 import enterprise_api, student_api

api_router = APIRouter()


@api_router.get("/runtime-identity", tags=["runtime"])
def runtime_identity() -> dict[str, str]:
    """Public non-sensitive build identity for browser evidence."""
    return {"status": "ok", "build_commit": settings.build_commit}


api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
api_router.include_router(jobs.router)
api_router.include_router(profiles.router)
api_router.include_router(llm_batches.router)
api_router.include_router(assist.router)
api_router.include_router(imports.router)
api_router.include_router(fairs.router)
api_router.include_router(scene.router)
api_router.include_router(visits.router)
api_router.include_router(reports.router)
api_router.include_router(student_access.router)
api_router.include_router(enterprise_access.router)

# Student and enterprise H5 routes are mounted in demo/production alike.
# Their own auth dependencies select the accepted credential family.
api_router.include_router(student_api.router)
api_router.include_router(enterprise_api.router)

_probe = APIRouter(prefix="/rbac", tags=["rbac-probe"])


@_probe.get("/admin-only")
def admin_only(user: CurrentUser = Depends(require_roles(Role.ADMIN))) -> dict:
    return {"ok": True, "scope": "admin", "user": user.username}


@_probe.get("/staff")
def staff_area(
    user: CurrentUser = Depends(
        require_roles(Role.ADMIN, Role.EMPLOYMENT_OFFICE, Role.COUNSELOR)
    ),
) -> dict:
    return {"ok": True, "scope": "staff", "user": user.username}


@_probe.get("/readonly")
def readonly_area(
    user: CurrentUser = Depends(
        require_roles(
            Role.ADMIN,
            Role.EMPLOYMENT_OFFICE,
            Role.COUNSELOR,
            Role.LEADER_READONLY,
        )
    ),
) -> dict:
    return {"ok": True, "scope": "readonly", "user": user.username}


api_router.include_router(_probe)
