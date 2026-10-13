"""T9 M6 scene statistics service.

The software boundary is aggregate-only metrics: no video stream, no biometrics,
and no individual trajectory data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_db
from app.core.config import require_demo_profile
from app.models import (
    College,
    Fair,
    FairCheckin,
    FairCompany,
    FairEnterpriseCheckin,
    SceneMetric,
    Student,
)
from app.services.fairs import FAIR_COMPANY_APPROVED
from app.services.write_idempotency import (
    WriteOperationConflict,
    validate_write_operation_key,
    write_request_hash,
)


class SceneSensorAdapter(Protocol):
    def push_metrics(
        self,
        zone: str,
        ts: datetime,
        headcount: int,
        dwell_avg_s: float | None,
    ) -> SceneMetric:
        """Push one aggregate metric row."""


class MockAdapter:
    def __init__(self, db: Session, *, fair_id: int, actor: str):
        self.db = db
        self.fair_id = fair_id
        self.actor = actor

    def push_metrics(
        self,
        zone: str,
        ts: datetime,
        headcount: int,
        dwell_avg_s: float | None,
    ) -> SceneMetric:
        return _insert_metric(
            self.db,
            fair_id=self.fair_id,
            zone=zone,
            ts=ts,
            headcount=headcount,
            dwell_avg_s=dwell_avg_s,
            actor=self.actor,
            source="mock",
        )


class HttpPushAdapter:
    def __init__(
        self,
        db: Session,
        *,
        fair_id: int,
        actor: str,
        idempotency_key: str,
        request_hash: str,
        actor_id: int | None,
        actor_role: str | None,
    ):
        self.db = db
        self.fair_id = fair_id
        self.actor = actor
        self.idempotency_key = idempotency_key
        self.request_hash = request_hash
        self.actor_id = actor_id
        self.actor_role = actor_role

    def push_metrics(
        self,
        zone: str,
        ts: datetime,
        headcount: int,
        dwell_avg_s: float | None,
    ) -> SceneMetric:
        return _insert_metric(
            self.db,
            fair_id=self.fair_id,
            zone=zone,
            ts=ts,
            headcount=headcount,
            dwell_avg_s=dwell_avg_s,
            actor=self.actor,
            source="http_push",
            idempotency_key=self.idempotency_key,
            request_hash=self.request_hash,
            actor_id=self.actor_id,
            actor_role=self.actor_role,
        )


def list_scene_fairs(db: Session) -> list[Fair]:
    return list(db.execute(select(Fair).order_by(Fair.held_at.desc().nullslast(), Fair.id)).scalars())


def seed_mock_scene_metrics(
    db: Session,
    *,
    fair_ids: list[int],
    actor: str,
) -> dict[int, dict]:
    # This function deletes prior metrics before inserting deterministic Mock
    # rows, so the profile guard must run before the first query or mutation.
    require_demo_profile("seed_mock_scene_metrics")
    expected: dict[int, dict] = {}
    zones = ["A区", "B区", "宣讲区"]
    for fair_index, fair_id in enumerate(fair_ids):
        _get_fair(db, fair_id)
        db.execute(delete(SceneMetric).where(SceneMetric.fair_id == fair_id))
        adapter = MockAdapter(db, fair_id=fair_id, actor=actor)
        base = datetime(2026, 3, 18, 9, tzinfo=UTC) + timedelta(days=fair_index)
        zone_heat = {zone: 0 for zone in zones}
        time_buckets: dict[str, int] = {}
        for zone_index, zone in enumerate(zones):
            for hour in range(3):
                headcount = 30 + fair_index * 10 + zone_index * 5 + hour
                ts = base + timedelta(hours=hour)
                adapter.push_metrics(zone, ts, headcount, 120.0 + zone_index * 30 + hour)
                zone_heat[zone] += headcount
                key = ts.isoformat()
                time_buckets[key] = time_buckets.get(key, 0) + headcount
        expected[fair_id] = {
            "total_headcount": sum(zone_heat.values()),
            "zone_heat": zone_heat,
            "time_buckets": time_buckets,
        }
    return expected


def push_http_metric(
    db: Session,
    *,
    fair_id: int,
    zone: str,
    ts: datetime,
    headcount: int,
    dwell_avg_s: float | None,
    actor: str,
    idempotency_key: str,
    actor_id: int | None = None,
    actor_role: str | None = None,
) -> tuple[SceneMetric, bool]:
    _get_fair(db, fair_id)
    idempotency_key = validate_write_operation_key(idempotency_key)
    request_hash = write_request_hash(
        "scene_metric.push",
        actor=actor,
        payload={
            "dwell_avg_s": dwell_avg_s,
            "fair_id": fair_id,
            "headcount": headcount,
            "ts": ts.isoformat(),
            "zone": zone,
        },
    )
    existing = _find_scene_metric_by_operation(db, idempotency_key)
    if existing is not None:
        _assert_same_operation(existing.request_hash, request_hash)
        return existing, True

    adapter = HttpPushAdapter(
        db,
        fair_id=fair_id,
        actor=actor,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        actor_id=actor_id,
        actor_role=actor_role,
    )
    try:
        metric = adapter.push_metrics(zone, ts, headcount, dwell_avg_s)
    except IntegrityError:
        # PostgreSQL's unique key elects one writer when identical retries race.
        # Roll back this losing transaction, then resolve the committed winner.
        db.rollback()
        _get_fair(db, fair_id)
        existing = _find_scene_metric_by_operation(db, idempotency_key)
        if existing is None:
            raise
        _assert_same_operation(existing.request_hash, request_hash)
        return existing, True
    db.commit()
    db.refresh(metric)
    return metric, False


def get_scene_metric_by_operation(
    db: Session,
    idempotency_key: str,
    *,
    actor: str,
) -> SceneMetric | None:
    idempotency_key = validate_write_operation_key(idempotency_key)
    return db.scalar(
        select(SceneMetric).where(
            SceneMetric.idempotency_key == idempotency_key,
            SceneMetric.created_by == actor,
        )
    )


def _find_scene_metric_by_operation(
    db: Session,
    idempotency_key: str,
) -> SceneMetric | None:
    """Resolve a replay globally; callers must verify its request hash."""

    return db.scalar(
        select(SceneMetric).where(SceneMetric.idempotency_key == idempotency_key)
    )


def _assert_same_operation(actual_hash: str | None, expected_hash: str) -> None:
    if actual_hash != expected_hash:
        raise WriteOperationConflict(
            "Idempotency-Key was already used for a different scene metric request"
        )


def build_scene_report(db: Session, fair_id: int) -> dict:
    fair = _get_fair(db, fair_id)
    named_checkins, anonymous_count = _attendance_counts(db, fair_id)
    total_students = db.execute(select(func.count()).select_from(Student)).scalar_one()
    college_rows = db.execute(
        select(College.name, func.count(func.distinct(Student.id)))
        .join(Student, Student.college_id == College.id)
        .join(FairCheckin, FairCheckin.student_id == Student.id)
        .where(FairCheckin.fair_id == fair_id)
        .group_by(College.name)
        .order_by(College.name)
    ).all()
    key_group_count = db.execute(
        select(func.count(func.distinct(Student.id)))
        .join(FairCheckin, FairCheckin.student_id == Student.id)
        .where(
            FairCheckin.fair_id == fair_id,
            Student.is_key_group.is_not(None),
        )
    ).scalar_one()
    enterprise_aggregate = db.execute(
        select(
            func.count(func.distinct(FairCompany.id)),
            func.count(func.distinct(FairCompany.id)).filter(
                FairCompany.status == FAIR_COMPANY_APPROVED
            ),
            func.count(func.distinct(FairCompany.id)).filter(
                FairCompany.booth.is_not(None),
                func.length(func.trim(FairCompany.booth)) > 0,
            ),
            func.count(func.distinct(FairEnterpriseCheckin.fair_company_id)),
            func.count(FairEnterpriseCheckin.id),
        )
        .select_from(FairCompany)
        .outerjoin(
            FairEnterpriseCheckin,
            FairEnterpriseCheckin.fair_company_id == FairCompany.id,
        )
        .where(FairCompany.fair_id == fair_id)
    ).one()
    zone_heat = {
        zone: int(total)
        for zone, total in db.execute(
            select(SceneMetric.zone, func.coalesce(func.sum(SceneMetric.headcount), 0))
            .where(SceneMetric.fair_id == fair_id)
            .group_by(SceneMetric.zone)
            .order_by(SceneMetric.zone)
        ).all()
    }
    time_buckets = {
        ts.isoformat(): int(total)
        for ts, total in db.execute(
            select(SceneMetric.ts_bucket, func.coalesce(func.sum(SceneMetric.headcount), 0))
            .where(SceneMetric.fair_id == fair_id)
            .group_by(SceneMetric.ts_bucket)
            .order_by(SceneMetric.ts_bucket)
        ).all()
    }
    avg_dwell = db.execute(
        select(func.avg(SceneMetric.dwell_avg_s)).where(SceneMetric.fair_id == fair_id)
    ).scalar_one()
    checked_in = int(named_checkins or 0) + int(anonymous_count or 0)
    return {
        "fair_id": fair.id,
        "fair_name": fair.name,
        "fair_type": fair.fair_type,
        "identity": {
            "checked_in_students": checked_in,
            "named_checkins": int(named_checkins or 0),
            "anonymous_count": int(anonymous_count or 0),
            "attendance_rate": round(100.0 * checked_in / max(int(total_students or 0), 1), 2),
            "college_distribution": {name: int(count) for name, count in college_rows},
            "key_group_checked_in": int(key_group_count or 0),
        },
        "enterprise": {
            "registered_companies": int(enterprise_aggregate[0] or 0),
            "approved_companies": int(enterprise_aggregate[1] or 0),
            "assigned_booths": int(enterprise_aggregate[2] or 0),
            "checked_in_companies": int(enterprise_aggregate[3] or 0),
            "representative_checkins": int(enterprise_aggregate[4] or 0),
        },
        "total_headcount": sum(zone_heat.values()),
        "avg_dwell_s": round(float(avg_dwell), 2) if avg_dwell is not None else None,
        "zone_heat": zone_heat,
        "time_buckets": time_buckets,
        "same_type_comparison": _same_type_comparison(
            db,
            fair=fair,
            total_students=int(total_students or 0),
        ),
    }


def _attendance_counts(db: Session, fair_id: int) -> tuple[int, int]:
    named = db.execute(
        select(func.count(func.distinct(FairCheckin.student_id))).where(
            FairCheckin.fair_id == fair_id,
            FairCheckin.student_id.is_not(None),
        )
    ).scalar_one()
    anonymous = db.execute(
        select(func.coalesce(func.sum(FairCheckin.anonymous_count), 0)).where(
            FairCheckin.fair_id == fair_id,
            FairCheckin.anonymous_count.is_not(None),
        )
    ).scalar_one()
    return int(named or 0), int(anonymous or 0)


def _same_type_comparison(
    db: Session,
    *,
    fair: Fair,
    total_students: int,
) -> list[dict]:
    peers = list(
        db.execute(
            select(Fair)
            .where(Fair.fair_type == fair.fair_type)
            .order_by(Fair.held_at.desc().nullslast(), Fair.id.desc())
        ).scalars()
    )
    rows: list[dict] = []
    for peer in peers:
        named, anonymous = _attendance_counts(db, peer.id)
        headcount = db.execute(
            select(func.coalesce(func.sum(SceneMetric.headcount), 0)).where(
                SceneMetric.fair_id == peer.id
            )
        ).scalar_one()
        rows.append(
            {
                "fair_id": peer.id,
                "fair_name": peer.name,
                "fair_type": peer.fair_type,
                "held_at": peer.held_at,
                "attendance_rate": round(
                    100.0 * (named + anonymous) / max(total_students, 1),
                    2,
                ),
                "total_headcount": int(headcount or 0),
            }
        )
    return rows


def _insert_metric(
    db: Session,
    *,
    fair_id: int,
    zone: str,
    ts: datetime,
    headcount: int,
    dwell_avg_s: float | None,
    actor: str,
    source: str,
    idempotency_key: str | None = None,
    request_hash: str | None = None,
    actor_id: int | None = None,
    actor_role: str | None = None,
) -> SceneMetric:
    row = SceneMetric(
        fair_id=fair_id,
        zone=zone,
        ts_bucket=ts,
        headcount=headcount,
        dwell_avg_s=dwell_avg_s,
        note=source,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        created_by=actor,
    )
    db.add(row)
    db.flush()
    record_db(
        db,
        actor=actor,
        actor_id=actor_id,
        actor_role=actor_role,
        source_channel="management.scene" if idempotency_key else "seed.scene",
        correlation_id=f"SCENE-METRIC-{idempotency_key}" if idempotency_key else None,
        action="scene_metric.push",
        object_type="scene_metric",
        object_id=row.id,
        change_summary={
            "fair_id": fair_id,
            "zone": zone,
            "ts": ts.isoformat(),
            "headcount": headcount,
            "source": source,
        },
    )
    return row


def _get_fair(db: Session, fair_id: int) -> Fair:
    fair = db.get(Fair, fair_id)
    if fair is None:
        raise ValueError("fair not found")
    return fair
