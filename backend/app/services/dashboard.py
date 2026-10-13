"""驾驶舱物化视图刷新与读取（SPEC §6.1）。

刷新策略：写触发（导入/去向变更/预警重算后调用）+ 定时（部署侧 cron/BackgroundTasks
调 refresh_dashboard_views）。读路径只查 mv_dashboard_*，保证首屏 <1s。
"""

from __future__ import annotations

import math

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.thresholds import load_employment_target_rate

DASHBOARD_VIEWS = (
    "mv_dashboard_summary",
    "mv_dashboard_colleges",
    "mv_dashboard_trend",
    "mv_dashboard_alerts",
)


def refresh_dashboard_views(
    db: Session,
    *,
    concurrently: bool = True,
    commit: bool = True,
) -> None:
    """刷新全部驾驶舱物化视图。concurrently 需各 MV 已有唯一索引（迁移已建）。"""
    mode = "CONCURRENTLY " if concurrently else ""
    for view in DASHBOARD_VIEWS:
        db.execute(text(f"REFRESH MATERIALIZED VIEW {mode}{view}"))
    if commit:
        db.commit()
    else:
        db.flush()


def get_summary(db: Session) -> dict:
    row = db.execute(text("SELECT * FROM mv_dashboard_summary")).mappings().first()
    if row is None:
        return {}
    summary = dict(row)
    facts = db.execute(text("""
        WITH business_cutoff AS (
            SELECT max(value) AS value
            FROM (
                SELECT max(status_occurred_at) AS value FROM students
                UNION ALL
                SELECT max(occurred_at) AS value FROM job_actions
                UNION ALL
                SELECT max(held_at) AS value FROM activities
            ) dates
        ), recent_settled AS (
            SELECT count(DISTINCT student_id) AS count
            FROM (
                SELECT id AS student_id, status_occurred_at AS occurred_at
                FROM students
                WHERE status IN ('签约', '升学', '出国', '入伍', '灵活就业')
                  AND status_occurred_at IS NOT NULL
                UNION ALL
                SELECT student_id, occurred_at
                FROM job_actions
                WHERE type = '签约'
            ) facts
            CROSS JOIN business_cutoff cutoff
            WHERE facts.occurred_at >= cutoff.value - interval '7 days'
              AND facts.occurred_at <= cutoff.value
        )
        SELECT
            round(
                100.0 * count(*) FILTER (
                    WHERE nullif(trim(student_no), '') IS NOT NULL
                      AND nullif(trim(name), '') IS NOT NULL
                      AND college_id IS NOT NULL
                      AND major_id IS NOT NULL
                      AND cohort_year IS NOT NULL
                      AND nullif(trim(status), '') IS NOT NULL
                ) / nullif(count(*), 0),
                1
            ) AS data_completeness_rate,
            (SELECT value FROM business_cutoff) AS data_cutoff_at,
            (SELECT count FROM recent_settled) AS recent_7d_settled_count
        FROM students
    """)).mappings().one()
    summary.update(dict(facts))
    summary["data_completeness_rate"] = float(
        summary.get("data_completeness_rate") or 0
    )
    summary["recent_7d_settled_count"] = int(
        summary.get("recent_7d_settled_count") or 0
    )
    target_rate = load_employment_target_rate(db)
    total = int(summary.get("total_students") or 0)
    settled = int(summary.get("settled_count") or 0)
    # “未落实”是管理口径：凡未进入五类已落实状态者均需继续对账，
    # 包括“暂不就业”。物化视图保留独立 not_seeking_count 供分析。
    summary["unsettled_count"] = max(0, total - settled)
    summary["target_rate"] = target_rate
    if target_rate is None:
        summary["target_gap_count"] = None
        summary["target_gap_rate"] = None
    else:
        summary["target_gap_count"] = max(
            0,
            math.ceil(total * target_rate / 100) - settled,
        )
        summary["target_gap_rate"] = max(
            0.0,
            round(target_rate - float(summary.get("settlement_rate") or 0), 1),
        )
    if summary.get("data_cutoff_at") is None:
        summary["data_cutoff_at"] = summary.get("refreshed_at")
    summary["settlement_definition"] = "已落实 = 签约、升学、出国、入伍、灵活就业"
    return summary


def get_colleges(db: Session) -> list[dict]:
    rows = db.execute(
        text("SELECT * FROM mv_dashboard_colleges ORDER BY college_id")
    ).mappings().all()
    result = []
    for row in rows:
        item = dict(row)
        item["unsettled_count"] = max(
            0,
            int(item.get("total") or 0)
            - int(item.get("settled") or 0),
        )
        result.append(item)
    return result


def get_college_summary(db: Session, college_id: int) -> dict:
    """辅导员学院级首页口径；不读取或返回全校汇总。"""
    row = db.execute(
        text("""
            WITH business_cutoff AS (
                SELECT max(value) AS value
                FROM (
                    SELECT max(status_occurred_at) AS value
                    FROM students WHERE college_id = :college_id
                    UNION ALL
                    SELECT max(ja.occurred_at) AS value
                    FROM job_actions ja
                    JOIN students s ON s.id = ja.student_id
                    WHERE s.college_id = :college_id
                ) dates
            ), recent_settled AS (
                SELECT count(DISTINCT student_id) AS count
                FROM (
                    SELECT id AS student_id, status_occurred_at AS occurred_at
                    FROM students
                    WHERE college_id = :college_id
                      AND status IN ('签约', '升学', '出国', '入伍', '灵活就业')
                      AND status_occurred_at IS NOT NULL
                    UNION ALL
                    SELECT ja.student_id, ja.occurred_at
                    FROM job_actions ja
                    JOIN students s ON s.id = ja.student_id
                    WHERE s.college_id = :college_id AND ja.type = '签约'
                ) facts
                CROSS JOIN business_cutoff cutoff
                WHERE facts.occurred_at >= cutoff.value - interval '7 days'
                  AND facts.occurred_at <= cutoff.value
            )
            SELECT
                c.id AS college_id,
                c.name AS college_name,
                count(s.id) AS total_students,
                count(s.id) FILTER (
                    WHERE s.status IN ('签约', '升学', '出国', '入伍', '灵活就业')
                ) AS settled_count,
                count(s.id) - count(s.id) FILTER (
                    WHERE s.status IN ('签约', '升学', '出国', '入伍', '灵活就业')
                ) AS unsettled_count,
                round(
                    100.0 * count(s.id) FILTER (
                        WHERE s.status IN ('签约', '升学', '出国', '入伍', '灵活就业')
                    ) / nullif(count(s.id), 0),
                    1
                ) AS settlement_rate,
                count(s.id) FILTER (WHERE s.risk_level = 'red') AS red_count,
                count(s.id) FILTER (WHERE s.risk_level = 'yellow') AS yellow_count,
                round(
                    100.0 * count(s.id) FILTER (
                        WHERE nullif(trim(s.student_no), '') IS NOT NULL
                          AND nullif(trim(s.name), '') IS NOT NULL
                          AND s.major_id IS NOT NULL
                          AND s.cohort_year IS NOT NULL
                          AND nullif(trim(s.status), '') IS NOT NULL
                    ) / nullif(count(s.id), 0),
                    1
                ) AS data_completeness_rate,
                (SELECT value FROM business_cutoff) AS data_cutoff_at,
                (SELECT count FROM recent_settled) AS recent_7d_settled_count
            FROM colleges c
            LEFT JOIN students s ON s.college_id = c.id
            WHERE c.id = :college_id
            GROUP BY c.id, c.name
        """),
        {"college_id": college_id},
    ).mappings().first()
    if row is None:
        return {}
    summary = dict(row)
    for key in (
        "total_students",
        "settled_count",
        "unsettled_count",
        "red_count",
        "yellow_count",
        "recent_7d_settled_count",
    ):
        summary[key] = int(summary.get(key) or 0)
    for key in ("settlement_rate", "data_completeness_rate"):
        summary[key] = float(summary.get(key) or 0)
    target_rate = load_employment_target_rate(db)
    summary["target_rate"] = target_rate
    if target_rate is None:
        summary["target_gap_count"] = None
        summary["target_gap_rate"] = None
    else:
        summary["target_gap_count"] = max(
            0,
            math.ceil(summary["total_students"] * target_rate / 100)
            - summary["settled_count"],
        )
        summary["target_gap_rate"] = max(
            0.0,
            round(target_rate - summary["settlement_rate"], 1),
        )
    summary["settlement_definition"] = "已落实 = 签约、升学、出国、入伍、灵活就业"
    return summary


def get_college_oversight(db: Session, college_id: int) -> dict:
    """校领导/就业中心学院督导视图；只返回汇总事实，不返回学生明细。"""
    summary = get_college_summary(db, college_id)
    if not summary:
        return {}
    row = db.execute(
        text("""
            WITH scoped_cases AS (
                SELECT ac.id, ac.student_id, ac.status
                FROM assist_cases ac
                JOIN students s ON s.id = ac.student_id
                WHERE s.college_id = :college_id
            ), raw_case_facts AS (
                SELECT
                    sc.id,
                    EXISTS (
                        SELECT 1 FROM assist_records ar WHERE ar.case_id = sc.id
                    ) AS assisted,
                    EXISTS (
                        SELECT 1 FROM matches m
                        WHERE m.student_id = sc.student_id AND m.pushed_at IS NOT NULL
                    ) AS pushed,
                    EXISTS (
                        SELECT 1 FROM matches m
                        WHERE m.student_id = sc.student_id
                          AND m.pushed_at IS NOT NULL
                          AND m.feedback IS NOT NULL
                    ) AS feedback,
                    EXISTS (
                        SELECT 1 FROM students s
                        WHERE s.id = sc.student_id AND s.status <> '未落实'
                    ) AS result
                FROM scoped_cases sc
            ), case_facts AS (
                SELECT
                    id,
                    assisted,
                    assisted AND pushed AS pushed,
                    assisted AND pushed AND feedback AS feedback,
                    assisted AND pushed AND feedback AND result AS result
                FROM raw_case_facts
            )
            SELECT
                count(*) AS discovered,
                count(*) FILTER (WHERE assisted) AS assisted,
                count(*) FILTER (WHERE pushed) AS pushed,
                count(*) FILTER (WHERE feedback) AS feedback,
                count(*) FILTER (WHERE result) AS result,
                (SELECT count(*) FROM scoped_cases WHERE status = '进行中') AS active_cases,
                (
                    SELECT count(*)
                    FROM assist_records ar
                    JOIN scoped_cases sc ON sc.id = ar.case_id
                ) AS assist_records,
                (
                    SELECT max(value)
                    FROM (
                        SELECT max(ar.occurred_at) AS value
                        FROM assist_records ar
                        JOIN scoped_cases sc ON sc.id = ar.case_id
                        UNION ALL
                        SELECT max(m.pushed_at) AS value
                        FROM matches m
                        JOIN scoped_cases sc ON sc.student_id = m.student_id
                        UNION ALL
                        SELECT max(s.status_occurred_at) AS value
                        FROM students s
                        WHERE s.college_id = :college_id
                    ) last_actions
                ) AS last_action_at
            FROM case_facts
        """),
        {"college_id": college_id},
    ).mappings().one()
    facts = {key: int(row.get(key) or 0) for key in (
        "discovered", "assisted", "pushed", "feedback", "result",
        "active_cases", "assist_records",
    )}
    summary.update(
        workflow={key: facts[key] for key in (
            "discovered", "assisted", "pushed", "feedback", "result"
        )},
        active_cases=facts["active_cases"],
        assist_records=facts["assist_records"],
        last_action_at=row.get("last_action_at"),
    )
    return summary


def get_trend(db: Session, date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    sql = "SELECT month, signings, actions FROM mv_dashboard_trend"
    clauses = []
    params: dict = {}
    if date_from:
        clauses.append("month >= :date_from")
        params["date_from"] = date_from
    if date_to:
        clauses.append("month <= :date_to")
        params["date_to"] = date_to
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY month"
    rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def get_alerts(db: Session, limit: int = 50, *, college_id: int | None = None) -> list[dict]:
    sql = "SELECT * FROM mv_dashboard_alerts"
    params: dict[str, int] = {"lim": limit}
    if college_id is not None:
        sql += " WHERE college_id = :college_id"
        params["college_id"] = college_id
    sql += " LIMIT :lim"
    rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]
