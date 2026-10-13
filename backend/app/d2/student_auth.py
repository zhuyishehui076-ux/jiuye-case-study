"""S1 D2 profile guard and restricted student JWT dependency."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import oauth2_scheme
from app.core.config import settings
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import Student, StudentAccount, StudentBinding, StudentSession
from app.services.student_identity import session_jti_digest


class CurrentStudent:
    def __init__(
        self,
        student_id: int,
        student_no: str,
        credential_release_id: str | None = None,
        account_id: int | None = None,
        session_jti: str | None = None,
    ):
        self.student_id = student_id
        self.student_no = student_no
        self.credential_release_id = credential_release_id
        self.account_id = account_id
        self.session_jti = session_jti

    @property
    def actor(self) -> str:
        return f"student:{self.student_id}"


def get_current_student(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> CurrentStudent:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效或过期的学生凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        raise credentials_error from None
    if payload.get("scope") != "student" or payload.get("role") != "student":
        raise credentials_error
    student_id = payload.get("student_id")
    student_no = payload.get("student_no")
    credential_version = payload.get("credential_version")
    credential_release_id = payload.get("credential_release_id")
    session_jti = payload.get("jti")
    if (
        not isinstance(student_id, int)
        or isinstance(student_id, bool)
        or not isinstance(student_no, str)
        or not isinstance(credential_version, int)
        or isinstance(credential_version, bool)
        or (
            credential_release_id is not None
            and (
                not isinstance(credential_release_id, str)
                or re.fullmatch(r"demo-[0-9a-f]{32}", credential_release_id) is None
            )
        )
    ):
        raise credentials_error
    if settings.student_production_auth_enabled:
        if credential_release_id is not None or not isinstance(session_jti, str):
            raise credentials_error
        try:
            parsed_jti = UUID(session_jti)
        except ValueError:
            raise credentials_error from None
        if parsed_jti.version != 4 or str(parsed_jti) != session_jti:
            raise credentials_error
        row = db.execute(
            select(StudentAccount.id)
            .join(Student, Student.id == StudentAccount.student_id)
            .join(StudentSession, StudentSession.account_id == StudentAccount.id)
            .where(
                StudentAccount.student_id == student_id,
                StudentAccount.status == "active",
                StudentAccount.credential_version == credential_version,
                Student.student_no == student_no,
                StudentSession.student_id == student_id,
                StudentSession.jti_digest == session_jti_digest(session_jti),
                StudentSession.revoked_at.is_(None),
                StudentSession.expires_at > datetime.now(UTC),
            )
        ).scalar_one_or_none()
        if row is None:
            raise credentials_error
        return CurrentStudent(
            student_id=student_id,
            student_no=student_no,
            account_id=row,
            session_jti=session_jti,
        )
    active = db.execute(
        select(StudentBinding.id)
        .join(Student, Student.id == StudentBinding.student_id)
        .where(
            StudentBinding.student_id == student_id,
            StudentBinding.is_active.is_(True),
            StudentBinding.credential_version == credential_version,
            Student.student_no == student_no,
        )
    ).scalar_one_or_none()
    if active is None:
        raise credentials_error
    return CurrentStudent(
        student_id=student_id,
        student_no=student_no,
        credential_release_id=credential_release_id,
    )
