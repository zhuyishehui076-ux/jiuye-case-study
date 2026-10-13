"""M1 驾驶舱测试（真实 PG）：物化视图刷新 + 四端点 + 红绿灯与 risk_level 一致（SPEC §6.1）。"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.conftest import login


@pytest.fixture(scope="module")
def dashboard_ready(pg_engine):
    """种子 + 刷新物化视图（写触发路径）。"""
    from app.db.session import SessionLocal
    from tests.conftest import reset_case_demo_seed
    from app.services.dashboard import refresh_dashboard_views

    with SessionLocal() as s:
        reset_case_demo_seed(s)
        refresh_dashboard_views(s)
    return True


def test_summary_all_cards_have_data(dashboard_ready):
    from app.db.session import SessionLocal
    from app.services.dashboard import get_summary

    with SessionLocal() as s:
        m = get_summary(s)
    assert m["total_students"] >= 2000
    assert m["settlement_rate"] > 0  # 落实率
    assert m["supply_ratio"] > 0  # 岗位供给比
    assert m["red_count"] > 0  # 红色预警数
    assert m["recent_activities"] > 0  # 本月活动数
    assert m["total_jobs"] == 300


def test_college_lights_consistent_with_risk_level(dashboard_ready):
    """红绿灯表保留的红/黄/绿学生计数必须与 students.risk_level 逐院一致。"""
    from app.db.session import SessionLocal
    from app.services.dashboard import get_colleges

    with SessionLocal() as s:
        rows = get_colleges(s)
        for r in rows:
            direct = s.execute(
                text(
                    "SELECT count(*) FILTER (WHERE risk_level='red') red, "
                    "count(*) FILTER (WHERE risk_level='yellow') yellow, "
                    "count(*) FILTER (WHERE risk_level='green') green "
                    "FROM students WHERE college_id=:cid"
                ),
                {"cid": r["college_id"]},
            ).mappings().one()
            assert r["red"] == direct["red"]
            assert r["yellow"] == direct["yellow"]
            assert r["green"] == direct["green"]


def test_dashboard_light_thresholds_seeded_in_app_settings(dashboard_ready):
    """院系红绿灯阈值必须落 app_settings，供校方按 risk 阈值同机制调整。"""
    from app.db.session import SessionLocal

    with SessionLocal() as s:
        row = s.execute(
            text("SELECT value FROM app_settings WHERE key='dashboard_light_thresholds'")
        ).mappings().one_or_none()

    assert row is not None
    assert row["value"] == {
        "red_below": 60.0,
        "yellow_min": 60.0,
        "green_above": 75.0,
    }


def test_college_light_uses_settlement_rate_thresholds(dashboard_ready):
    """灯色按落实率阈值判定：红 <60%、黄 60-75%、绿 >75%。"""
    from app.db.session import SessionLocal
    from app.services.dashboard import get_colleges, refresh_dashboard_views
    from app.services.thresholds import load_dashboard_light_thresholds

    with SessionLocal() as s:
        thresholds = load_dashboard_light_thresholds(s)
        targets = s.execute(
            text("SELECT id FROM colleges ORDER BY id LIMIT 3")
        ).scalars().all()
        scenarios = [
            (targets[0], 0.50, "red"),
            (targets[1], 0.70, "yellow"),
            (targets[2], 0.90, "green"),
        ]
        for cid, ratio, _expected in scenarios:
            total = s.execute(
                text("SELECT count(*) FROM students WHERE college_id=:cid"),
                {"cid": cid},
            ).scalar_one()
            settled_n = int(total * ratio)
            s.execute(
                text(
                    "UPDATE students SET status='未落实', risk_level='red' "
                    "WHERE college_id=:cid"
                ),
                {"cid": cid},
            )
            s.execute(
                text(
                    "UPDATE students SET status='签约' "
                    "WHERE id IN ("
                    "  SELECT id FROM students WHERE college_id=:cid "
                    "  ORDER BY id LIMIT :lim"
                    ")"
                ),
                {"cid": cid, "lim": settled_n},
            )
        s.commit()
        refresh_dashboard_views(s)
        rows = {r["college_id"]: r for r in get_colleges(s)}

    for cid, _ratio, expected in scenarios:
        row = rows[cid]
        assert row["red"] == row["total"]
        assert row["light"] == expected
        if expected == "red":
            assert row["settlement_rate"] < thresholds["red_below"]
        elif expected == "yellow":
            assert thresholds["yellow_min"] <= row["settlement_rate"] <= thresholds["green_above"]
        else:
            assert row["settlement_rate"] > thresholds["green_above"]


def test_trend_has_points(dashboard_ready):
    from app.db.session import SessionLocal
    from app.services.dashboard import get_trend

    with SessionLocal() as s:
        pts = get_trend(s)
    assert len(pts) > 0
    assert sum(p["signings"] for p in pts) > 0


def test_alerts_only_red_yellow(dashboard_ready):
    from app.db.session import SessionLocal
    from app.services.dashboard import get_alerts

    with SessionLocal() as s:
        alerts = get_alerts(s, limit=500)
    assert len(alerts) > 0
    assert all(a["risk_level"] in ("red", "yellow") for a in alerts)


def test_refresh_reflects_writes(dashboard_ready):
    """写触发刷新：改 risk_level → 刷新 → summary 红数变化。"""
    from app.db.session import SessionLocal
    from app.services.dashboard import get_summary, refresh_dashboard_views

    with SessionLocal() as s:
        before = get_summary(s)["red_count"]
        s.execute(text(
            "UPDATE students SET risk_level='red' WHERE risk_level='green' "
            "AND id IN (SELECT id FROM students WHERE risk_level='green' LIMIT 5)"
        ))
        s.commit()
        refresh_dashboard_views(s)
        after = get_summary(s)["red_count"]
    assert after == before + 5
    # 还原
    with SessionLocal() as s:
        refresh_dashboard_views(s)


# ---------------- 端点 + RBAC（leader_readonly 可见，无写入口） ----------------


def test_all_roles_read_dashboard(client, dashboard_ready, demo_passwords):
    for user in ('admin', 'leader'):
        tok = login(client, user, demo_passwords[user])
        h = {"Authorization": f"Bearer {tok}"}
        for path in ["/api/v1/dashboard/summary", "/api/v1/dashboard/colleges",
                     "/api/v1/dashboard/trend", "/api/v1/dashboard/alerts"]:
            r = client.get(path, headers=h)
            assert r.status_code == 200, f"{user} {path} {r.status_code} {r.text}"


def test_dashboard_has_no_write_routes(client):
    # leader_readonly 无任何写入口：/dashboard 下不存在非 GET 路由
    from app.main import app

    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if "/dashboard" in path:
            assert methods <= {"GET", "HEAD"}, f"{path} 暴露了写方法 {methods}"


def test_summary_endpoint_shape(client, dashboard_ready, demo_passwords):
    tok = login(client, "admin", demo_passwords['admin'])
    r = client.get("/api/v1/dashboard/summary", headers={"Authorization": f"Bearer {tok}"})
    body = r.json()
    for k in ("total_students", "settlement_rate", "supply_ratio", "red_count", "recent_activities"):
        assert k in body
