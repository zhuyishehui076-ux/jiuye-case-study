"""FastAPI 应用入口（SPEC §2）。

启动即触发配置层守卫：导入 app.core.config 时 Settings() 构造。
production 缺少生产密钥/PostgreSQL，或 local 配置外部 LLM 时直接拒绝启动。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.http_security import SecurityHeadersMiddleware
from app.db.session import SessionLocal
from app.services.installation import verify_installation_profile
from app.services.storage_namespace import verify_storage_namespace


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """T1 起不再在启动期建表（移除 create_all）。

    表结构由 Alembic 迁移建立（`alembic upgrade head`）。在业务服务可用前，
    先验证上传卷归属，再使用独立会话校验或首次持久化数据库安装身份；
    此处绝不调用 create_all。
    """
    if settings.installation_id != "development":
        verify_storage_namespace(
            settings.upload_dir,
            kind="uploads",
            expected_profile=settings.profile,
            expected_installation_id=settings.installation_id,
        )
    with SessionLocal() as db:
        verify_installation_profile(
            db,
            expected_profile=settings.profile,
            expected_installation_id=settings.installation_id,
        )
    yield


production = settings.profile.value == "production"
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if production else "/docs",
    redoc_url=None if production else "/redoc",
    openapi_url=None if production else "/openapi.json",
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.add_middleware(SecurityHeadersMiddleware)
app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/health")
def health() -> dict:
    """存活状态与非敏感构建身份，供发布证据绑定当前 Git HEAD。"""
    return {"status": "ok", "build_commit": settings.build_commit}


@app.get("/ready")
def readiness(response: Response) -> dict:
    """Prove a reversible PostgreSQL write without exposing dependency details."""
    try:
        with SessionLocal() as db:
            db.execute(
                text(
                    "INSERT INTO ops_readiness_probes (probe_id, checked_at) "
                    "VALUES (:probe_id, clock_timestamp())"
                ),
                {"probe_id": uuid4()},
            )
            # The write must reach PostgreSQL so read-only roles, WAL/disk
            # failures and schema drift fail readiness. It is always rolled
            # back and never becomes a business or audit fact.
            db.rollback()
    except SQLAlchemyError:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "build_commit": settings.build_commit}
    return {"status": "ready", "build_commit": settings.build_commit}


@app.get("/profile")
def profile_info() -> dict:
    """暴露当前档位能力开关（前端角标/写入口判断依据；不泄露密钥）。"""
    return {
        "profile": settings.profile.value,
        "deployment_mode": settings.deployment.value,
        "show_demo_badge": settings.show_demo_badge,
        "allow_external_llm": settings.allow_external_llm,
        "network_isolated": settings.network_isolated,
        "llm_batch_enabled": settings.llm_batch_enabled,
        "d2_demo_routes_enabled": settings.d2_demo_routes_enabled,
        "student_demo_auth_enabled": settings.student_demo_auth_enabled,
        "student_production_auth_enabled": settings.student_production_auth_enabled,
    }
