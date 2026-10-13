"""鉴权相关 Pydantic schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.services.auth_security import validate_password_policy


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime | None = None


class LogoutResponse(BaseModel):
    logged_out: bool = True


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)

    @model_validator(mode="after")
    def enforce_policy(self) -> PasswordChangeRequest:
        validate_password_policy(self.new_password)
        return self


class PasswordChangeResponse(BaseModel):
    changed: bool = True
    sessions_revoked: int


class UserOut(BaseModel):
    username: str
    display_name: str
    role: str
    college_id: int | None = None
    college_name: str | None = None


class MeResponse(UserOut):
    # 前端据此决定是否常显演示角标 / 是否隐藏写入口
    show_demo_badge: bool
    profile: str
    llm_batch_enabled: bool
