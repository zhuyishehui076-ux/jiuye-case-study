"""JD 解析编排（SPEC §5 三段式：LLM 抽取 → 规则映射专业代码 → 置信度/兜底）。

parse_jd 是可测的编排函数：LLM 抽取交给 provider.complete_json，专业名→代码走规则映射
（禁止 LLM 直接出代码）；低置信或校验失败 → 标记 needs_review 进人工复核队列（产品兜底）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.prompts.jd_parse import (
    JD_PARSE_PROMPT_VER,
    JD_PARSE_SYSTEM,
    build_jd_user_prompt,
)
from app.llm.provider import Provider
from app.llm.schemas import JD_PARSE_SCHEMA
from app.models import Major

# 低于此置信度进人工复核队列（兜底，非失败）
REVIEW_CONFIDENCE_THRESHOLD = 0.6


@dataclass
class JdParseResult:
    degree_req: str | None = None
    major_names: list[str] = field(default_factory=list)
    major_codes: list[str] = field(default_factory=list)
    direction_keywords: list[str] = field(default_factory=list)
    location: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    headcount: int | None = None
    deadline: str | None = None
    parse_confidence: float = 0.0
    needs_review: bool = True
    review_reason: str | None = None
    prompt_ver: str = JD_PARSE_PROMPT_VER


def build_major_index(db: Session) -> dict[str, str]:
    """专业名 → 代码索引（规范名精确匹配用）。"""
    rows = db.execute(select(Major.name, Major.code)).all()
    return {name: code for name, code in rows}


def map_major_names(names: list[str], index: dict[str, str]) -> tuple[list[str], int]:
    """规则映射专业名→代码：精确匹配优先，其次子串包含。返回 (codes, matched_count)。"""
    codes: list[str] = []
    matched = 0
    for raw in names:
        n = (raw or "").strip()
        if not n:
            continue
        code = index.get(n)
        if code is None:
            # 子串包含：JD 里 "计算机" 命中 "计算机科学与技术"
            for cand_name, cand_code in index.items():
                if n in cand_name or cand_name in n:
                    code = cand_code
                    break
        if code is not None:
            matched += 1
            if code not in codes:
                codes.append(code)
    return codes, matched


def _confidence(degree_req: str | None, location: str | None,
                major_names: list[str], matched: int) -> float:
    # 专业缺失即真实失败模式：map_rate=0（不再默认放行）
    map_rate = (matched / len(major_names)) if major_names else 0.0
    degree_ok = 1.0 if degree_req in {"本科", "硕士", "博士"} else 0.0
    loc_ok = 1.0 if location else 0.0
    return round(0.5 * map_rate + 0.25 * degree_ok + 0.25 * loc_ok, 3)


def parse_jd(provider: Provider, raw_text: str, major_index: dict[str, str]) -> JdParseResult:
    system = JD_PARSE_SYSTEM
    user = build_jd_user_prompt(raw_text)
    try:
        data = provider.complete_json(JD_PARSE_SCHEMA, system, user)
    except Exception as e:
        return JdParseResult(needs_review=True, review_reason=f"解析失败：{e}", parse_confidence=0.0)

    major_names = data.get("major_names") or []
    codes, matched = map_major_names(major_names, major_index)
    conf = _confidence(data.get("degree_req"), data.get("location"), major_names, matched)

    # 裁决#1：专业口径未识别（LLM 未给出专业名）是必须接住的真实失败模式，强制进复核队列。
    if not major_names:
        needs_review, review_reason = True, "未识别专业口径"
    elif conf < REVIEW_CONFIDENCE_THRESHOLD:
        needs_review, review_reason = True, "置信度低于阈值，转人工复核"
    else:
        needs_review, review_reason = False, None

    return JdParseResult(
        degree_req=data.get("degree_req"),
        major_names=major_names,
        major_codes=codes,
        direction_keywords=data.get("direction_keywords") or [],
        location=data.get("location"),
        salary_min=data.get("salary_min"),
        salary_max=data.get("salary_max"),
        headcount=data.get("headcount"),
        deadline=data.get("deadline"),
        parse_confidence=conf,
        needs_review=needs_review,
        review_reason=review_reason,
    )
