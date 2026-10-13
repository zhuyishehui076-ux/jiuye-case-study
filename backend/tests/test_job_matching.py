"""T4 M4 岗位解析与智能匹配测试（SPEC §6.4）。"""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from tests.conftest import login


@pytest.fixture(scope="module")
def t4_ready(pg_engine):
    from app.db.session import SessionLocal
    from app.seed.demo import run_demo_seed
    from app.seed.users import seed_demo_users

    with SessionLocal() as s:
        run_demo_seed(s, reset=True)
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
        if row is None:
            row = AppSetting(
                key="job_match_weights",
                value={},
                description="test override",
                created_by="test",
            )
            s.add(row)
        row.value = {"major": 1.0, "direction": 0.0, "hard": 0.0}
        s.commit()
        weights = load_match_weights(s)

    assert weights.major == 1.0
    assert weights.direction == 0.0
    assert weights.hard == 0.0


def test_review_flow_updates_parse_and_writes_audit_log(t4_ready):
    from app.db.session import SessionLocal
    from app.models import AuditLog, Job, JobParse
    from app.services.jobs import review_job_parse

    with SessionLocal() as s:
        job = s.execute(select(Job).order_by(Job.id)).scalars().first()
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

        updated = review_job_parse(
            s,
            parse.id,
            actor="office",
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


def test_job_entry_endpoints_cover_paste_xlsx_and_email_stub(client, t4_ready, demo_passwords):
    office_token = login(client, "office", demo_passwords['office'])
    leader_token = login(client, "leader", demo_passwords['leader'])
    office_headers = {"Authorization": f"Bearer {office_token}"}
    leader_headers = {"Authorization": f"Bearer {leader_token}"}

    pasted = client.post(
        "/api/v1/jobs/paste",
        headers=office_headers,
        json={
            "company": "测试企业",
            "title": "算法工程师",
            "raw_text": "本科，计算机相关专业，北京，负责人工智能算法开发。",
        },
    )
    assert pasted.status_code == 200
    assert pasted.json()["source"] == "表单"

    wb = Workbook()
    ws = wb.active
    ws.append(["company", "title", "raw_text"])
    ws.append(["批量企业A", "后端工程师", "本科，软件工程相关专业，上海，负责平台开发。"])
    ws.append(["批量企业B", "数据分析师", "本科，统计学相关专业，杭州，负责数据建模。"])
    buf = BytesIO()
    wb.save(buf)
    imported = client.post(
        "/api/v1/jobs/import-xlsx",
        headers=office_headers,
        files={
            "file": (
                "jobs.xlsx",
                buf.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert imported.status_code == 200
    assert len(imported.json()) == 2
    assert client.post("/api/v1/jobs/email-pull", headers=office_headers).status_code == 501
    assert client.post("/api/v1/jobs/paste", headers=leader_headers, json={
        "company": "只读企业",
        "title": "只读岗位",
        "raw_text": "leader_readonly 不应可写",
    }).status_code == 403


def test_push_confirm_and_export_match_list(client, t4_ready, demo_passwords):
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
        calculate_matches_for_job(s, job_id, actor="office", limit=10)
        s.commit()

    office_token = login(client, "office", demo_passwords['office'])
    headers = {"Authorization": f"Bearer {office_token}"}
    pushed = client.post(f"/api/v1/jobs/{job_id}/push", headers=headers)
    assert pushed.status_code == 200
    assert pushed.json()["pushed"] == 10

    exported = client.get(f"/api/v1/jobs/{job_id}/export", headers=headers)
    assert exported.status_code == 200
    assert "text/csv" in exported.headers["content-type"]
    body = exported.text
    assert "student_no,student_name,college_id,major_id,score,reason_text" in body
    assert "专业代码" in body
