"""Fail-closed projection helpers for data shown to a student."""

from __future__ import annotations

import math
import re
import unicodedata

from app.services.profiles import PROFILE_DIMENSIONS

_STUDENT_UNSAFE = re.compile(
    r"预警|风险(?:等级|原因)?|risk[\s_-]*(?:level|reason)|"
    r"is[\s_-]*key[\s_-]*group|key[\s_-]*group|"
    r"low[\s_-]*income|disabled|minority[\s_-]*remote|long[\s_-]*unemployed|"
    r"困难群体|重点群体|低收入|残疾|少数民族(?:边远)?|边远地区|"
    r"长期未就业|(?:红|黄|绿)(?:色)?(?:预警|风险|等级|档|标签|灯|学生)|"
    r"(?:等级|预警|风险|档|标签|灯)\s*(?:为|是|[:：])?\s*(?:红|黄|绿)|"
    r"(?<![\u4e00-\u9fff])(?:红色|黄色|绿色)(?![\u4e00-\u9fff])|"
    r"(?:为|是|属于|标记为)\s*(?:红|黄|绿)(?:色)?(?![\u4e00-\u9fff])|"
    r"(?<![\u4e00-\u9fff])(?:红|黄|绿)(?![\u4e00-\u9fff])|"
    r"(?<![A-Za-z0-9_])(?:red|yellow|green)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_COMPACT_UNSAFE = (
    "risklevel",
    "riskreason",
    "iskeygroup",
    "keygroup",
    "lowincome",
    "minorityremote",
    "longunemployed",
    "disabled",
    "预警",
    "风险",
    "困难群体",
    "重点群体",
    "低收入",
    "残疾",
    "少数民族",
    "边远地区",
    "长期未就业",
)

DEFAULT_STUDENT_SUGGESTIONS = (
    "完善项目成果说明",
    "持续记录求职进展",
    "关注专业相关岗位",
)
DEFAULT_RECOMMENDATION_REASON = "岗位条件与个人方向匹配"


def _is_unsafe_student_text(text: str) -> bool:
    if _STUDENT_UNSAFE.search(text):
        return True
    compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text.casefold())
    return any(marker in compact for marker in _COMPACT_UNSAFE)


def safe_student_text(value: object, *, fallback: str) -> str:
    """Return a student-safe string or a non-sensitive product fallback."""
    if not isinstance(value, str):
        return fallback
    text = unicodedata.normalize("NFKC", value).strip()
    if not text or _is_unsafe_student_text(text):
        return fallback
    return text


def safe_recommendation_reason(value: object) -> str:
    return safe_student_text(value, fallback=DEFAULT_RECOMMENDATION_REASON)


def safe_student_suggestions(value: object) -> list[str]:
    """Never project free-form management/LLM evidence to a student.

    Internal profile evidence may encode a protected risk or hardship label in
    arbitrarily paraphrased language.  A blacklist therefore cannot establish
    the required fail-closed boundary.  T18 intentionally returns only the
    repository-owned, non-sensitive action templates; richer personalised
    copy must wait for a separately typed and allowlisted fact model.
    """
    del value
    return list(DEFAULT_STUDENT_SUGGESTIONS)


def safe_profile_dimensions(value: object) -> dict[str, float]:
    """Allow only the six product dimensions and finite scores in [0, 100]."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for key in PROFILE_DIMENSIONS:
        raw = value.get(key)
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            continue
        score = float(raw)
        if math.isfinite(score) and 0 <= score <= 100:
            result[key] = score
    return result
