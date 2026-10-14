"""Isolated production-profile upload probe, called only by the guarded fixture.

No demo seeds, dependency overrides, settings reloads, external models or
existing production resources. Output contains only synthetic result evidence.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
from urllib.parse import urlsplit
from uuid import uuid4


class ProbeFailure(RuntimeError):
    pass


def require(condition, label):
    if not condition:
        raise ProbeFailure(label)


def validate_target(phase):
    name = os.environ.get("JIUYE_PROD_PROBE_DATABASE", "")
    require(re.fullmatch(r"jiuye_case_prod_test_[0-9a-f]{32}", name), "database.name")
    nonce = name.removeprefix("jiuye_case_prod_test_")
    role = f"case_prod_{nonce}"
    require(os.environ.get("JIUYE_PROD_PROBE_ROLE") == role, "database.role")
    require(os.environ.get("INSTALLATION_ID") == f"jiuye-case-production-test-{nonce}", "installation.name")
    url = urlsplit(os.environ.get("DATABASE_URL", ""))
    require(url.scheme == "postgresql+psycopg" and url.hostname == "postgres" and url.port == 5432,
            "database.server")
    require(url.path == f"/{name}" and not url.query and not url.fragment, "database.target")
    require(url.username == ("case_user" if phase == "initialize" else role), "database.actor")
    require(url.password and len(url.password) >= 32, "database.password")
    require(os.environ.get("APP_PROFILE") == "production" and os.environ.get("DEPLOYMENT_MODE") == "local",
            "profile.target")
    require(len(os.environ.get("JWT_SECRET", "")) >= 32
            and len(os.environ.get("FIELD_ENCRYPTION_KEY", "")) >= 32, "secrets.missing")
    require(len({url.password, os.environ["JWT_SECRET"], os.environ["FIELD_ENCRYPTION_KEY"]}) == 3,
            "secrets.independent")
    require(re.fullmatch(r"/tmp/jiuye-prod-import-probe-[A-Za-z0-9_-]+/uploads",
                         os.environ.get("UPLOAD_DIR", "")), "uploads.target")
    return name, nonce


def run(phase):
    name, nonce = validate_target(phase)
    from sqlalchemy import func, select, text

    from app.core.config import Profile, settings
    from app.db.session import SessionLocal, engine
    from app.models import AuditLog, Job, OperationReceipt
    from app.models.user import Role, User
    from app.services.installation import (
        ensure_installation_profile, preflight_existing_database, verify_installation_profile,
    )
    from app.services.storage_namespace import (
        ensure_storage_namespace, preflight_storage_namespace,
    )

    identity = dict(expected_profile=Profile.PRODUCTION, expected_installation_id=settings.installation_id)
    try:
        with SessionLocal() as db:
            require(db.scalar(text("SELECT current_database()")) == name, "database.connected_identity")
            if phase == "initialize":
                require(preflight_existing_database(db, **identity) == "fresh", "database.not_fresh")
            else:
                verify_installation_profile(db, **identity)
                require(db.scalar(text("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")) is False,
                        "database.runtime_superuser")
        if phase == "initialize":
            from alembic import command
            from alembic.config import Config
            from app.core.security import hash_password

            preflight_storage_namespace(settings.upload_dir, kind="uploads", **identity)
            command.upgrade(Config("alembic.ini"), "head")
            with SessionLocal() as db:
                ensure_installation_profile(db, **identity)
                db.commit()
                for surface, role_name in (("OFFICE", "employment_office"), ("LEADER", "leader_readonly")):
                    role = db.scalar(select(Role).where(Role.name == role_name))
                    if role is None:
                        role = Role(name=role_name, created_by="production-test")
                        db.add(role)
                        db.flush()
                    db.add(User(
                        username=f"probe-{surface.lower()}-{nonce}", display_name=f"Synthetic {surface}",
                        password_hash=hash_password(os.environ[f"JIUYE_PROD_PROBE_{surface}_PASSWORD"]),
                        role_id=role.id, created_by="production-test", is_active=True,
                    ))
                db.commit()
            ensure_storage_namespace(settings.upload_dir, kind="uploads", **identity)
            return {"profile": "production", "initialized": True}

        from fastapi.testclient import TestClient
        from app.main import app

        workbook = base64.b64decode(sys.stdin.read(), validate=True)
        with TestClient(app) as client:
            require(not app.dependency_overrides, "dependencies.overridden")
            require(client.get("/profile").json()["profile"] == "production", "http.profile")
            headers = {}
            for surface in ("OFFICE", "LEADER"):
                response = client.post("/api/v1/auth/login", data={
                    "username": f"probe-{surface.lower()}-{nonce}",
                    "password": os.environ[f"JIUYE_PROD_PROBE_{surface}_PASSWORD"],
                })
                require(response.status_code == 200, f"auth.{surface.lower()}")
                headers[surface] = {"Authorization": f"Bearer {response.json()['access_token']}"}
                require(client.get("/api/v1/auth/me", headers=headers[surface]).status_code == 200,
                        "auth.session")

            endpoint = "/api/v1/jobs/import-xlsx"
            files = {"file": ("synthetic-jobs.xlsx", workbook,
                              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
            require(client.post(endpoint, files=files).status_code == 401, "auth.required")
            require(client.post(endpoint, headers={**headers["LEADER"], "Idempotency-Key": str(uuid4())},
                                files=files).status_code == 403, "role.leader")
            require(client.post(endpoint, headers=headers["OFFICE"], files=files).status_code == 422,
                    "idempotency.required")
            key = str(uuid4())
            operation = {**headers["OFFICE"], "Idempotency-Key": key}
            imported = client.post(endpoint, headers=operation, files=files)
            require(imported.status_code == 200, "import.status")
            rows = imported.json()
            require(len(rows) == 2, "import.rows")
            require(all(row["source"] == "导入" for row in rows), "import.source")
            replay = client.post(endpoint, headers=operation, files=files)
            require(replay.status_code == 200 and replay.json() == rows, "import.replay")
            with SessionLocal() as db:
                require(db.scalar(select(func.count()).select_from(Job)) == 2, "import.persisted_count")
                require(db.scalar(select(func.count()).select_from(AuditLog).where(
                    AuditLog.action == "job.import_xlsx")) == 1, "audit.once")
                require(db.scalar(select(func.count()).select_from(OperationReceipt).where(
                    OperationReceipt.namespace == "job.import_xlsx")) == 1, "receipt.once")
                verify_installation_profile(db, **identity)
            return {"profile": "production", "status_code": imported.status_code, "rows": rows,
                    "persisted_count": 2, "replay_status_code": replay.status_code, "audit_count": 1,
                    "runtime_superuser": False}
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        phase = sys.argv[1]
        require(phase in {"initialize", "verify"}, "phase.invalid")
        print(json.dumps(run(phase), ensure_ascii=False))
    except Exception as exc:
        label = str(exc) if isinstance(exc, ProbeFailure) else type(exc).__name__
        print(f"PRODUCTION_PROBE_FAILED:{label}", file=sys.stderr)
        raise SystemExit(1) from None
