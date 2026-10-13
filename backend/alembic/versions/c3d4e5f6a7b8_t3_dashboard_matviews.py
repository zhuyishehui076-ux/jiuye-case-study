"""t3 M1 驾驶舱物化视图 mv_dashboard_*

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-07-07 00:00:00.000000

SPEC §6.1：四端点全部读物化视图。时间窗相对"数据最新月"（种子锚定 2025，
避免依赖 wall-clock 导致空卡）。每个 MV 建唯一索引以支持 REFRESH ... CONCURRENTLY。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SETTLED = "('签约','升学','出国','入伍','灵活就业')"

SUMMARY = f"""
CREATE MATERIALIZED VIEW mv_dashboard_summary AS
SELECT
  1 AS id,
  count(*) AS total_students,
  count(*) FILTER (WHERE status IN {SETTLED}) AS settled_count,
  count(*) FILTER (WHERE status = '未落实') AS unsettled_count,
  count(*) FILTER (WHERE status = '签约') AS signed_count,
  count(*) FILTER (WHERE status = '升学') AS further_study_count,
  count(*) FILTER (WHERE status = '出国') AS abroad_count,
  count(*) FILTER (WHERE status = '入伍') AS military_count,
  count(*) FILTER (WHERE status = '灵活就业') AS flexible_count,
  count(*) FILTER (WHERE status = '暂不就业') AS not_seeking_count,
  round(100.0 * count(*) FILTER (WHERE status IN {SETTLED}) / greatest(count(*), 1), 1) AS settlement_rate,
  count(*) FILTER (WHERE risk_level = 'red') AS red_count,
  count(*) FILTER (WHERE risk_level = 'yellow') AS yellow_count,
  count(*) FILTER (WHERE risk_level = 'green') AS green_count,
  (SELECT count(*) FROM jobs) AS total_jobs,
  round((SELECT count(*) FROM jobs)::numeric
        / greatest(count(*) FILTER (WHERE status = '未落实'), 1), 2) AS supply_ratio,
  (SELECT count(*) FROM activities
     WHERE held_at >= (SELECT max(held_at) FROM activities) - interval '30 days') AS recent_activities,
  now() AS refreshed_at
FROM students;
"""

COLLEGES = f"""
CREATE MATERIALIZED VIEW mv_dashboard_colleges AS
WITH light_thresholds AS (
  SELECT
    COALESCE((value->>'red_below')::numeric, 60.0) AS red_below,
    COALESCE((value->>'yellow_min')::numeric, 60.0) AS yellow_min,
    COALESCE((value->>'green_above')::numeric, 75.0) AS green_above
  FROM app_settings
  WHERE key = 'dashboard_light_thresholds'
  UNION ALL
  SELECT 60.0, 60.0, 75.0
  WHERE NOT EXISTS (
    SELECT 1 FROM app_settings WHERE key = 'dashboard_light_thresholds'
  )
),
college_rates AS (
  SELECT
    c.id AS college_id,
    c.name AS college_name,
    count(s.id) AS total,
    count(*) FILTER (WHERE s.status IN {SETTLED}) AS settled,
    round(100.0 * count(*) FILTER (WHERE s.status IN {SETTLED}) / greatest(count(s.id), 1), 1) AS settlement_rate,
    count(*) FILTER (WHERE s.risk_level = 'red') AS red,
    count(*) FILTER (WHERE s.risk_level = 'yellow') AS yellow,
    count(*) FILTER (WHERE s.risk_level = 'green') AS green
  FROM colleges c
  LEFT JOIN students s ON s.college_id = c.id
  GROUP BY c.id, c.name
)
SELECT
  cr.college_id,
  cr.college_name,
  cr.total,
  cr.settled,
  cr.settlement_rate,
  cr.red,
  cr.yellow,
  cr.green,
  CASE
    WHEN cr.total = 0 THEN 'green'
    WHEN cr.settlement_rate < t.red_below THEN 'red'
    WHEN cr.settlement_rate >= t.yellow_min
      AND cr.settlement_rate <= t.green_above THEN 'yellow'
    WHEN cr.settlement_rate > t.green_above THEN 'green'
    ELSE 'yellow'
  END AS light
FROM college_rates cr
CROSS JOIN light_thresholds t;
"""

TREND = """
CREATE MATERIALIZED VIEW mv_dashboard_trend AS
SELECT
  date_trunc('month', occurred_at)::date AS month,
  count(*) FILTER (WHERE type = '签约') AS signings,
  count(*) AS actions
FROM job_actions
GROUP BY 1
ORDER BY 1;
"""

ALERTS = """
CREATE MATERIALIZED VIEW mv_dashboard_alerts AS
SELECT
  s.id AS student_id,
  s.student_no,
  s.name,
  s.college_id,
  c.name AS college_name,
  s.risk_level,
  s.risk_reason,
  s.status
FROM students s
JOIN colleges c ON c.id = s.college_id
WHERE s.risk_level IN ('red', 'yellow')
ORDER BY (s.risk_level = 'red') DESC, s.id;
"""

VIEWS = ["mv_dashboard_summary", "mv_dashboard_colleges", "mv_dashboard_trend", "mv_dashboard_alerts"]
UNIQUE_IDX = {
    "mv_dashboard_summary": ("id",),
    "mv_dashboard_colleges": ("college_id",),
    "mv_dashboard_trend": ("month",),
    "mv_dashboard_alerts": ("student_id",),
}


def upgrade() -> None:
    for sql in (SUMMARY, COLLEGES, TREND, ALERTS):
        op.execute(sql)
    for view, cols in UNIQUE_IDX.items():
        op.execute(f"CREATE UNIQUE INDEX uq_{view} ON {view} ({', '.join(cols)})")


def downgrade() -> None:
    for view in reversed(VIEWS):
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {view}")
