"""T4 M4 岗位解析与智能匹配测试（SPEC §6.4）。"""

from __future__ import annotations

import csv
from io import BytesIO, StringIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from tests.conftest import login


@pytest.fixture(scope="module")
def t4_ready(pg_engine):
    from app.db.session import SessionLocal
    from tests.conftest import reset_case_demo_seed
    from app.seed.users import seed_demo_users

    with SessionLocal() as s:
        reset_case_demo_seed(s)
        seed_demo_users(s)
    return True


def test_match_score_uses_configured_formula_and_reason_text():
    from app.services.matching import MatchWeights, score_student_match

    job_parse = SimpleNamespace(
        major_codes=["080901"],
        direction_keywords=["人工智能"],
        degree_req="本科",
        location="北京",
    )
    student = SimpleNamespace(
        degree_level="本科",
        origin_place="北京",
        major=SimpleNamespace(code="080901"),
        direction=SimpleNamespace(keywords=["人工智能", "机器学习"], embedding=None),
    )

    result = score_student_match(
        job_parse,
        student,
        MatchWeights(major=0.5, direction=0.3, hard=0.2),
    )

    assert result.score == 100.0
    assert result.reason_text
    for fragment in ("专业代码命中", "方向", "学历", "地点"):
        assert fragment in result.reason_text


def test_match_weights_are_loaded_from_app_settings(t4_ready):
    from app.db.session import SessionLocal
    from app.models.setting import AppSetting
    from app.services.matching import load_match_weights

    with SessionLocal() as s:
        row = s.execute(
            select(AppSetting).where(AppSetting.key == "job_match_weights")
        ).scalar_one_or_none()
        previous_value = dict(row.value) if row is not None else None
        if row is None:
            row = AppSetting(
                key="job_match_weights",
                value={},
                description="test override",
                created_by="test",
            )
            s.add(row)
        row.value = {"major": 1.0, "direction": 0.0, "hard": 0.0}
        # 只在此事务内验证覆盖值；关闭会话时回滚，避免污染后续推送用例。
        s.flush()
        weights = load_match_weights(s)

    assert weights.major == 1.0
    assert weights.direction == 0.0
    assert weights.hard == 0.0
    with SessionLocal() as s:
        persisted_value = s.scalar(
            select(AppSetting.value).where(AppSetting.key == "job_match_weights")
        )
    assert persisted_value == previous_value


def test_review_flow_updates_parse_and_writes_audit_log(t4_ready):
    from app.db.session import SessionLocal
    from app.models import AuditLog, Job, JobParse
    from app.services.jobs import JobParseReviewConflictError, review_job_parse

    with SessionLocal() as s:
        template = s.execute(select(Job).order_by(Job.id)).scalars().first()
        job = Job(
            company="Synthetic Review Company",
            title="Synthetic Review Position",
            raw_text="Synthetic review regression; no real employer data.",
            source=template.source,
            received_at=template.received_at,
            created_by="test",
        )
        s.add(job)
        s.flush()
        parse = JobParse(
            job_id=job.id,
            degree_req=None,
            major_codes=[],
            direction_keywords=[],
            location=None,
            parse_confidence=0.2,
            needs_review=True,
            review_reason="未识别专业口径",
            llm_meta={"provider": "test"},
            created_by="test",
        )
        s.add(parse)
        s.commit()
        expected_revision = parse.review_revision

        updated = review_job_parse(
            s,
            parse.id,
            actor="office",
            expected_revision=expected_revision,
            updates={
                "degree_req": "本科",
                "major_codes": ["080901"],
                "direction_keywords": ["人工智能"],
                "location": "北京",
                "needs_review": False,
                "review_reason": None,
            },
        )
        audit = s.execute(
            select(AuditLog).where(
                AuditLog.action == "job_parse.review",
                AuditLog.object_id == str(parse.id),
            ).order_by(AuditLog.id.desc())
        ).scalars().first()
        assert updated.review_revision == expected_revision + 1
        assert audit.change_summary["before"]["review_revision"] == expected_revision
        assert audit.change_summary["after"]["review_revision"] == expected_revision + 1
        with pytest.raises(JobParseReviewConflictError):
            review_job_parse(
                s,
                parse.id,
                actor="office",
                expected_revision=expected_revision,
                updates={"major_codes": []},
            )
        assert updated.major_codes == ["080901"]

    assert updated.needs_review is False
    assert updated.major_codes == ["080901"]
    assert audit.actor_name == "office"
    assert audit.change_summary["after"]["major_codes"] == ["080901"]


def test_parse_job_reuses_t2_pipeline_and_maps_major_codes(t4_ready):
    from app.db.session import SessionLocal
    from app.models import Job, JobParse
    from app.services.jobs import parse_job

    class ProviderStub:
        name = "stub"

        def complete_json(self, *_args, **_kwargs):
            return {
                "degree_req": "本科",
                "major_names": ["计算机科学与技术"],
                "direction_keywords": ["人工智能"],
                "location": "北京",
                "salary_min": 18,
                "salary_max": 28,
                "headcount": 3,
                "deadline": None,
            }

        def embed(self, _texts):
            return []

    with SessionLocal() as s:
        job = s.execute(
            select(Job).where(~Job.parse.has()).order_by(Job.id)
        ).scalars().first()
        parsed = parse_job(s, job.id, ProviderStub(), actor="office")
        stored = s.execute(select(JobParse).where(JobParse.job_id == job.id)).scalar_one()

    assert parsed.id == stored.id
    assert "080901" in parsed.major_codes
    assert parsed.needs_review is False


def test_parse_job_provider_error_enters_review_queue(t4_ready):
    from app.db.session import SessionLocal
    from app.llm.provider import LLMError
    from app.models import Job, JobParse
    from app.services.jobs import parse_job

    class FailingProvider:
        name = "failing"

        def complete_json(self, *_args, **_kwargs):
            raise LLMError("demo provider unavailable")

        def embed(self, _texts):
            return []

    with SessionLocal() as s:
        job = s.execute(
            select(Job).where(~Job.parse.has()).order_by(Job.id)
        ).scalars().first()
        parsed = parse_job(s, job.id, FailingProvider(), actor="office")
        stored = s.execute(select(JobParse).where(JobParse.job_id == job.id)).scalar_one()

    assert parsed.id == stored.id
    assert parsed.needs_review is True
    assert parsed.parse_confidence == 0.0
    assert "demo provider unavailable" in (parsed.review_reason or "")


def test_counselor_match_list_is_limited_to_own_college(client, t4_ready, demo_passwords):
    from app.db.session import SessionLocal
    from app.models import Job, JobParse
    from app.services.jobs import calculate_matches_for_job

    with SessionLocal() as s:
        job = s.execute(
            select(Job).where(~Job.parse.has()).order_by(Job.id)
        ).scalars().first()
        s.add(
            JobParse(
                job_id=job.id,
                degree_req=None,
                major_codes=[],
                direction_keywords=[],
                location=None,
                parse_confidence=0.9,
                needs_review=False,
                llm_meta={"provider": "test"},
                created_by="test",
            )
        )
        s.commit()
        job_id = job.id
        calculate_matches_for_job(s, job_id, actor="office", limit=40)
        s.commit()

    counselor_token = login(client, "counselor", demo_passwords['counselor'])
    office_token = login(client, "office", demo_passwords['office'])
    counselor_rows = client.get(
        f"/api/v1/jobs/{job_id}/matches",
        headers={"Authorization": f"Bearer {counselor_token}"},
    ).json()
    office_rows = client.get(
        f"/api/v1/jobs/{job_id}/matches",
        headers={"Authorization": f"Bearer {office_token}"},
    ).json()

    assert counselor_rows
    assert all(r["college_id"] == 1 for r in counselor_rows)
    assert len(office_rows) >= len(counselor_rows)
    assert any(r["college_id"] != 1 for r in office_rows)
    assert all(r["reason_text"] for r in office_rows)


def test_job_entry_endpoints_cover_paste_xlsx_and_email_stub(
    client, t4_ready, demo_passwords, production_import_probe,
):
    office_token = login(client, "office", demo_passwords['office'])
    leader_token = login(client, "leader", demo_passwords['leader'])
    office_headers = {"Authorization": f"Bearer {office_token}"}
    leader_headers = {"Authorization": f"Bearer {leader_token}"}

    pasted = client.post(
        "/api/v1/jobs/paste",
        headers={**office_headers, "Idempotency-Key": str(uuid4())},
        json={
            "company": "测试企业",
            "title": "算法工程师",
            "raw_text": "本科，计算机相关专业，北京，负责人工智能算法开发。",
        },
    )
    assert pasted.status_code == 200, pasted.text
    assert pasted.json()["source"] == "表单"
    # Demo 与 production 分别在自己的库、身份和进程中验证真实边界。
    assert client.post("/api/v1/jobs/email-pull", headers=office_headers).status_code == 501
    assert client.post("/api/v1/jobs/paste", headers={
        **leader_headers, "Idempotency-Key": str(uuid4()),
    }, json={
        "company": "只读企业",
        "title": "只读岗位",
        "raw_text": "leader_readonly 不应可写",
    }).status_code == 403

    wb = Workbook()
    ws = wb.active
    ws.append(["company", "title", "raw_text"])
    ws.append(["批量企业A", "后端工程师", "本科，软件工程相关专业，上海，负责平台开发。"])
    ws.append(["批量企业B", "数据分析师", "本科，统计学相关专业，杭州，负责数据建模。"])
    buf = BytesIO()
    wb.save(buf)
    rejected = client.post(
        "/api/v1/jobs/import-xlsx",
        headers={**office_headers, "Idempotency-Key": str(uuid4())},
        files={
            "file": (
                "jobs.xlsx",
                buf.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert rejected.status_code == 403, rejected.text
    imported = production_import_probe(buf.getvalue())
    assert imported["profile"] == "production"
    assert imported["status_code"] == 200
    assert len(imported["rows"]) == 2
    assert imported["persisted_count"] == 2
    assert imported["replay_status_code"] == 200
    assert imported["audit_count"] == 1
    assert imported["runtime_superuser"] is False


def test_push_confirm_and_export_match_list(client, t4_ready, demo_passwords):
    from app.db.session import SessionLocal
    from app.models import AuditLog, Job, JobParse, Major, Match
    from app.services.jobs import calculate_matches_for_job

    with SessionLocal() as s:
        job = s.execute(
            select(Job).where(~Job.parse.has()).order_by(Job.id)
        ).scalars().first()
        major_codes = list(s.scalars(select(Major.code).order_by(Major.id)))
        assert major_codes
        s.add(
            JobParse(
                job_id=job.id,
                degree_req=None,
                major_codes=major_codes,
                direction_keywords=[],
                location=None,
                parse_confidence=0.9,
                needs_review=False,
                llm_meta={"provider": "test"},
                created_by="test",
            )
        )
        s.commit()
        job_id = job.id
        matches = calculate_matches_for_job(s, job_id, actor="office", limit=10)
        assert len(matches) == 10
        assert all(float(row.score) > 0 and row.pushed_at is None for row in matches)
        s.commit()

    office_token = login(client, "office", demo_passwords['office'])
    headers = {"Authorization": f"Bearer {office_token}"}
    pushed = client.post(f"/api/v1/jobs/{job_id}/push", headers=headers)
    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["pushed"] == 10
    replayed = client.post(f"/api/v1/jobs/{job_id}/push", headers=headers)
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["pushed"] == 0
    assert replayed.json()["already_pushed"] == 10
    with SessionLocal() as s:
        rows = list(s.scalars(select(Match).where(Match.job_id == job_id)))
        push_audits = list(s.scalars(select(AuditLog).where(
            AuditLog.action == "job_match.push", AuditLog.object_id == str(job_id),
        )))
    assert len(rows) == 10
    assert all(row.pushed_at is not None for row in rows)
    assert len(push_audits) == 1

    exported = client.get(f"/api/v1/jobs/{job_id}/export", headers=headers)
    assert exported.status_code == 200
    assert "text/csv" in exported.headers["content-type"]
    assert exported.content.startswith(b"\xef\xbb\xbf")
    default_rows = list(csv.reader(StringIO(exported.text.lstrip("\ufeff"))))
    assert default_rows[0] == ["学号", "姓名", "学院编号", "专业编号", "匹配度", "推荐理由"]
    assert len(default_rows) == 11
    counselor = login(client, "counselor", demo_passwords["counselor"])
    for language in ("zh", "en"):
        response = client.get(
            f"/api/v1/jobs/{job_id}/export?header_language={language}", headers=headers,
        )
        assert response.status_code == 200, response.text
        assert "text/csv" in response.headers["content-type"]
        assert response.content.startswith(b"\xef\xbb\xbf")
        body = response.text
        if language == "en":
            assert "student_no,student_name,college_id,major_id,score,reason_text" in body
            assert "matches-en.csv" in response.headers["content-disposition"]
        assert "专业代码" in body
        rows = list(csv.reader(StringIO(body.lstrip("\ufeff"))))
        assert rows[1:] == default_rows[1:]
        scoped = client.get(
            f"/api/v1/jobs/{job_id}/export?header_language={language}",
            headers={"Authorization": f"Bearer {counselor}"},
        )
        assert scoped.status_code == 200, scoped.text
        scoped_rows = list(csv.reader(StringIO(scoped.text.lstrip("\ufeff"))))
        assert scoped_rows[0] == rows[0]
        assert scoped_rows[1:]
        assert all(row[2] == "1" for row in scoped_rows[1:])
        for user in ("admin", "leader"):
            token = login(client, user, demo_passwords[user])
            denied = client.get(
                f"/api/v1/jobs/{job_id}/export?header_language={language}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert denied.status_code == 403, denied.text
    invalid = client.get(
        f"/api/v1/jobs/{job_id}/export?header_language=unsupported", headers=headers,
    )
    assert invalid.status_code == 422, invalid.text
    with SessionLocal() as s:
        audits = list(s.scalars(select(AuditLog).where(
            AuditLog.action == "job_match.export", AuditLog.object_id == str(job_id),
        )))
    assert len(audits) == 5
    assert {row.change_summary["header_language"] for row in audits} == {"zh", "en"}
    assert sum(row.change_summary["college_scope"] == 1 for row in audits) == 2


def test_major_only_weights_with_no_major_hit_block_push(t4_ready, db):
    """复现旧推送夹具的零分条件；变更仅在可回滚的 PG 测试事务内。"""
    from app.models import AuditLog, Job, JobParse
    from app.models.setting import AppSetting
    from app.services.jobs import (
        MatchNotEligibleError,
        calculate_matches_for_job,
        confirm_push_matches,
    )

    weights = db.scalar(select(AppSetting).where(AppSetting.key == "job_match_weights"))
    weights.value = {"major": 1.0, "direction": 0.0, "hard": 0.0}
    job = db.scalars(select(Job).where(~Job.parse.has()).order_by(Job.id)).first()
    db.add(JobParse(
        job_id=job.id,
        major_codes=[],
        direction_keywords=[],
        needs_review=False,
        created_by="test",
    ))
    db.flush()
    matches = calculate_matches_for_job(db, job.id, actor="office", limit=10)
    assert len(matches) == 10
    assert all(float(row.score) == 0 for row in matches)
    with pytest.raises(MatchNotEligibleError, match="匹配度为 0"):
        confirm_push_matches(db, job.id, actor="office")
    assert all(row.pushed_at is None for row in matches)
    assert not list(db.scalars(select(AuditLog).where(
        AuditLog.action == "job_match.push", AuditLog.object_id == str(job.id),
    )))
