"""M1 驾驶舱测试（真实 PG）：物化视图刷新 + 四端点 + 红绿灯与 risk_level 一致（SPEC §6.1）。"""

from __future__ import annotations

from uuid import uuid4

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


# ---------------- 已确认的读取矩阵 + 就业办专用目标修改 ----------------


@pytest.mark.parametrize(
    "user,allowed",
    [
        ("admin", {"summary", "target"}),
        ("office", {"summary", "colleges", "trend", "alerts", "target", "colleges/1/oversight"}),
        ("counselor", {"alerts", "my-college-summary", "target"}),
        ("leader", {"summary", "colleges", "trend", "target", "colleges/1/oversight"}),
    ],
)
def test_dashboard_read_role_matrix(client, dashboard_ready, demo_passwords, user, allowed):
    tok = login(client, user, demo_passwords[user])
    h = {"Authorization": f"Bearer {tok}"}
    for endpoint in (
        "summary", "colleges", "trend", "alerts", "my-college-summary", "target",
        "colleges/1/oversight",
    ):
        r = client.get(f"/api/v1/dashboard/{endpoint}", headers=h)
        expected = 200 if endpoint in allowed else 403
        assert r.status_code == expected, f"{user} {endpoint} {r.status_code} {r.text}"
        if user == "counselor" and endpoint == "alerts":
            assert r.json()
            assert all(row["college_id"] == 1 for row in r.json())
        if user == "counselor" and endpoint == "my-college-summary":
            assert r.json()["college_id"] == 1


def test_dashboard_exposes_only_the_confirmed_target_write(client):
    from app.main import app

    writes = {
        (path, method)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/v1/dashboard/")
        for method in operations
        if method in {"post", "put", "patch", "delete"}
    }
    assert writes == {("/api/v1/dashboard/target", "put")}


def test_target_write_keeps_role_revision_idempotency_and_audit_guards(
    client, dashboard_ready, demo_passwords, db, monkeypatch,
):
    from sqlalchemy import func, select

    from app.db.session import get_db
    from app.main import app
    from app.models import AuditLog
    from app.models.setting import EMPLOYMENT_TARGET_KEY

    # 仅把真实PG会话接入可回滚事务；认证、角色和写入守卫仍执行原实现。
    def transaction_db():
        yield db

    monkeypatch.setitem(app.dependency_overrides, get_db, transaction_db)
    office = login(client, "office", demo_passwords["office"])
    auth = {"Authorization": f"Bearer {office}"}
    before = client.get("/api/v1/dashboard/target", headers=auth)
    assert before.status_code == 200, before.text
    audit_query = select(func.count()).select_from(AuditLog).where(
        AuditLog.action == "dashboard.target.update",
        AuditLog.object_id == EMPLOYMENT_TARGET_KEY,
    )
    audit_before = db.scalar(audit_query)
    payload = {
        "target_rate": 72.0,
        "note": "Synthetic CI target adjustment",
        "expected_revision": before.json()["revision"],
    }
    for user in ("admin", "counselor", "leader"):
        token = login(client, user, demo_passwords[user])
        denied = client.put("/api/v1/dashboard/target", json=payload, headers={
            "Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4()),
        })
        assert denied.status_code == 403, denied.text
        receipt_denied = client.get(
            f"/api/v1/dashboard/target-operations/{uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert receipt_denied.status_code == 403, receipt_denied.text

    assert client.put("/api/v1/dashboard/target", headers=auth, json=payload).status_code == 422
    assert client.put("/api/v1/dashboard/target", headers={
        **auth, "Idempotency-Key": "invalid-key",
    }, json=payload).status_code == 422
    assert client.put("/api/v1/dashboard/target", headers={
        **auth, "Idempotency-Key": str(uuid4()),
    }, json={"target_rate": 72.0, "note": payload["note"]}).status_code == 422

    key = str(uuid4())
    headers = {**auth, "Idempotency-Key": key}
    updated = client.put("/api/v1/dashboard/target", headers=headers, json=payload)
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == payload["expected_revision"] + 1
    assert updated.json()["target_rate"] == 72.0
    replay = client.put("/api/v1/dashboard/target", headers=headers, json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json() == updated.json()
    receipt = client.get(f"/api/v1/dashboard/target-operations/{key}", headers=auth)
    assert receipt.status_code == 200, receipt.text
    assert receipt.json() == updated.json()
    conflict = client.put(
        "/api/v1/dashboard/target", headers=headers, json={**payload, "target_rate": 73.0},
    )
    assert conflict.status_code == 409, conflict.text
    stale = client.put("/api/v1/dashboard/target", headers={
        **auth, "Idempotency-Key": str(uuid4()),
    }, json={**payload, "target_rate": 74.0})
    assert stale.status_code == 409, stale.text
    current = client.get("/api/v1/dashboard/target", headers=auth)
    assert current.status_code == 200, current.text
    assert current.json() == updated.json()
    assert db.scalar(audit_query) == audit_before + 1


def test_summary_endpoint_shape(client, dashboard_ready, demo_passwords):
    tok = login(client, "admin", demo_passwords['admin'])
    r = client.get("/api/v1/dashboard/summary", headers={"Authorization": f"Bearer {tok}"})
    body = r.json()
    for k in ("total_students", "settlement_rate", "supply_ratio", "red_count", "recent_activities"):
        assert k in body
