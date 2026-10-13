"""可配置项表 app_settings（SPEC §4：阈值全部进 settings 表可配置）。

键值对 + JSONB 值。risk 规则引擎阈值以 key='risk_thresholds' 落这张表；
院系红绿灯阈值以 key='dashboard_light_thresholds' 落这张表。校方可改；
业务纯函数/物化视图由加载器或 SQL 刷新时读取阈值。
"""

from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

RISK_THRESHOLDS_KEY = "risk_thresholds"
DASHBOARD_LIGHT_THRESHOLDS_KEY = "dashboard_light_thresholds"
JOB_MATCH_WEIGHTS_KEY = "job_match_weights"
PROFILE_SCORE_WEIGHTS_KEY = "profile_score_weights"
EMPLOYMENT_TARGET_KEY = "employment_target"


class AppSetting(Base, TimestampMixin):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
