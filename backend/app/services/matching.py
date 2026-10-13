"""T4 岗位匹配打分（SPEC §6.4）。

score = 0.5*专业代码命中 + 0.3*方向相似度 + 0.2*学历/地点硬条件。
权重从 app_settings.job_match_weights 读取，缺失时使用默认值。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.setting import JOB_MATCH_WEIGHTS_KEY, AppSetting


@dataclass(frozen=True)
class MatchWeights:
    major: float = 0.5
    direction: float = 0.3
    hard: float = 0.2


@dataclass(frozen=True)
class MatchScore:
    score: float
    reason_text: str


DEFAULT_MATCH_WEIGHTS = MatchWeights()


def load_match_weights(db: Session) -> MatchWeights:
    row = db.execute(
        select(AppSetting).where(AppSetting.key == JOB_MATCH_WEIGHTS_KEY)
    ).scalar_one_or_none()
    if row is None or not isinstance(row.value, dict):
        return DEFAULT_MATCH_WEIGHTS
    return MatchWeights(
        major=float(row.value.get("major", DEFAULT_MATCH_WEIGHTS.major)),
        direction=float(row.value.get("direction", DEFAULT_MATCH_WEIGHTS.direction)),
        hard=float(row.value.get("hard", DEFAULT_MATCH_WEIGHTS.hard)),
    )


def _seq(obj: Any, name: str) -> list:
    value = getattr(obj, name, None) or []
    return list(value)


def _cosine(a: list[float] | None, b: list[float] | None) -> float | None:
    if not a or not b or len(a) != len(b):
        return None
    denom = sqrt(sum(x * x for x in a)) * sqrt(sum(y * y for y in b))
    if denom == 0:
        return 0.0
    return max(0.0, min(1.0, sum(x * y for x, y in zip(a, b, strict=True)) / denom))


def _direction_similarity(job_parse: Any, student: Any) -> tuple[float, str | None]:
    parse_embedding = getattr(job_parse, "embedding", None)
    direction = getattr(student, "direction", None)
    direction_embedding = getattr(direction, "embedding", None) if direction else None
    cosine = _cosine(parse_embedding, direction_embedding)
    if cosine is not None:
        return cosine, "求职方向较符合" if cosine > 0 else None

    required = {str(k).strip() for k in _seq(job_parse, "direction_keywords") if str(k).strip()}
    owned = {
        str(k).strip()
        for k in (getattr(direction, "keywords", None) or [])
        if str(k).strip()
    }
    if not required:
        return 0.0, None
    hits = sorted(required & owned)
    similarity = len(hits) / len(required)
    if hits:
        return similarity, f"求职方向符合（{'、'.join(hits)}）"
    return 0.0, None


def _hard_condition(job_parse: Any, student: Any) -> tuple[float, list[str]]:
    degree_req = (getattr(job_parse, "degree_req", None) or "").strip()
    location_req = (getattr(job_parse, "location", None) or "").strip()
    degree_level = (getattr(student, "degree_level", None) or "").strip()
    origin_place = (getattr(student, "origin_place", None) or "").strip()

    degree_ok = not degree_req or degree_req == "不限" or degree_req == degree_level
    location_ok = (
        not location_req
        or location_req == "不限"
        or location_req in origin_place
        or origin_place in location_req
    )
    reasons: list[str] = []
    if degree_req and degree_ok:
        reasons.append("学历符合")
    elif not degree_req:
        reasons.append("学历不限")
    if location_req == "不限":
        reasons.append("地点不限")
    elif location_req and location_ok:
        reasons.append("地点符合")
    elif not location_req:
        reasons.append("地点不限")
    return (1.0 if degree_ok and location_ok else 0.0), reasons


def score_student_match(
    job_parse: Any,
    student: Any,
    weights: MatchWeights,
) -> MatchScore:
    major = getattr(student, "major", None)
    major_code = getattr(major, "code", None)
    major_name = getattr(major, "name", None)
    required_major_codes = set(_seq(job_parse, "major_codes"))
    major_hit = bool(major_code and major_code in required_major_codes)
    major_score = 1.0 if major_hit else 0.0

    reasons: list[str] = []
    if major_hit:
        reasons.append(f"专业代码命中（{major_name or '相关专业'}）")

    direction_score, direction_reason = _direction_similarity(job_parse, student)
    if direction_reason:
        reasons.append(direction_reason)

    hard_score, hard_reasons = _hard_condition(job_parse, student)
    reasons.extend(hard_reasons)
    score = round(
        100.0
        * (
            weights.major * major_score
            + weights.direction * direction_score
            + weights.hard * hard_score
        ),
        2,
    )
    return MatchScore(
        score=score,
        reason_text="；".join(reasons) if reasons else "暂无命中依据",
    )
