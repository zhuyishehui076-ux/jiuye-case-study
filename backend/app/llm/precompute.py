"""预计算管线（SPEC §5 / §7 T2）：批量解析种子库岗位 → 落库 job_parses。

P-DEMO 演示确定性依据：页面直接读预计算结果，"重新生成"按钮才走实时 provider。
用法：python -m app.llm.precompute [--limit N]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.llm.parsers.jd import build_major_index, parse_jd
from app.llm.provider import Provider, get_provider
from app.models import Job, JobParse


def _to_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def precompute_job_parses(
    db: Session, provider: Provider, *, limit: int | None = None
) -> dict[str, int]:
    """对尚无解析的岗位批量解析落库。返回统计。"""
    major_index = build_major_index(db)
    stmt = select(Job).where(~Job.parse.has()).order_by(Job.id)
    if limit:
        stmt = stmt.limit(limit)
    jobs = db.execute(stmt).scalars().all()

    stats = {"parsed": 0, "needs_review": 0, "failed": 0}
    for job in jobs:
        result = parse_jd(provider, job.raw_text, major_index)
        db.add(
            JobParse(
                job_id=job.id,
                degree_req=result.degree_req,
                major_codes=result.major_codes,
                direction_keywords=result.direction_keywords,
                location=result.location,
                salary_min=result.salary_min,
                salary_max=result.salary_max,
                headcount=result.headcount,
                deadline=_to_date(result.deadline),
                parse_confidence=result.parse_confidence,
                needs_review=result.needs_review,
                review_reason=result.review_reason,
                llm_meta={
                    "provider": provider.name,
                    "prompt_ver": result.prompt_ver,
                    "major_names": result.major_names,
                },
                created_by="precompute",
            )
        )
        stats["parsed"] += 1
        if result.needs_review:
            stats["needs_review"] += 1
        if result.parse_confidence == 0.0:
            stats["failed"] += 1
        db.commit()
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.llm.precompute")
    parser.add_argument("--limit", type=int, default=None, help="只解析前 N 条（调试/耐时控制）")
    args = parser.parse_args(argv)

    provider = get_provider()
    with SessionLocal() as db:
        stats = precompute_job_parses(db, provider, limit=args.limit)
    print(f"[precompute] provider={provider.name} 完成：{stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
