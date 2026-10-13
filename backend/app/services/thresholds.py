"""阈值加载器：从 app_settings 表读出可配置阈值。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.setting import (
    DASHBOARD_LIGHT_THRESHOLDS_KEY,
    EMPLOYMENT_TARGET_KEY,
    RISK_THRESHOLDS_KEY,
    AppSetting,
)
from app.services.risk import DEFAULT_RISK_THRESHOLDS

DEFAULT_DASHBOARD_LIGHT_THRESHOLDS: dict[str, float] = {
    "red_below": 60.0,
    "yellow_min": 60.0,
    "green_above": 75.0,
}

DEFAULT_EMPLOYMENT_TARGET_RATE = 90.0


def load_risk_thresholds(db: Session) -> dict[str, float]:
    """读 app_settings.risk_thresholds；缺失则回退默认（并入默认保证键完整）。"""
    row = db.execute(
        select(AppSetting).where(AppSetting.key == RISK_THRESHOLDS_KEY)
    ).scalar_one_or_none()
    if row is None or not isinstance(row.value, dict):
        return dict(DEFAULT_RISK_THRESHOLDS)
    return {**DEFAULT_RISK_THRESHOLDS, **row.value}


def load_dashboard_light_thresholds(db: Session) -> dict[str, float]:
    """读 app_settings.dashboard_light_thresholds；缺失则回退默认。"""
    row = db.execute(
        select(AppSetting).where(AppSetting.key == DASHBOARD_LIGHT_THRESHOLDS_KEY)
    ).scalar_one_or_none()
    if row is None or not isinstance(row.value, dict):
        return dict(DEFAULT_DASHBOARD_LIGHT_THRESHOLDS)
    return {**DEFAULT_DASHBOARD_LIGHT_THRESHOLDS, **row.value}


def load_employment_target(db: Session) -> dict[str, object]:
    """Return the auditable school target without inventing a production value.

    DEMO writes its explicit mock target during seed. A new PRODUCTION database
    remains unconfigured until the employment office records its own target.
    Persisted malformed values fail closed instead of silently becoming 90%.
    """
    row = db.execute(
        select(AppSetting).where(AppSetting.key == EMPLOYMENT_TARGET_KEY)
    ).scalar_one_or_none()
    if row is None:
        return {
            "target_rate": None,
            "revision": 0,
            "note": None,
            "updated_by": None,
            "updated_at": None,
            "configured": False,
        }
    if not isinstance(row.value, dict):
        raise ValueError("employment target setting must be a JSON object")
    value = row.value
    raw_rate = value.get("target_rate")
    if isinstance(raw_rate, bool) or not isinstance(raw_rate, int | float):
        raise ValueError("employment target rate must be numeric")
    target_rate = float(raw_rate)
    if not 0 <= target_rate <= 100:
        raise ValueError("employment target rate must be between 0 and 100")
    revision = value.get("revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("employment target revision must be a non-negative integer")
    note = value.get("note")
    if not isinstance(note, str) or len(note.strip()) < 2 or len(note.strip()) > 240:
        raise ValueError("employment target note must contain 2 to 240 characters")
    updated_by = value.get("updated_by")
    if not isinstance(updated_by, str) or not updated_by.strip():
        raise ValueError("employment target updated_by must be a non-empty string")
    return {
        "target_rate": target_rate,
        "revision": revision,
        "note": note.strip(),
        "updated_by": updated_by.strip(),
        "updated_at": row.updated_at if row is not None else None,
        "configured": True,
    }


def load_employment_target_rate(db: Session) -> float | None:
    value = load_employment_target(db)["target_rate"]
    return float(value) if value is not None else None
