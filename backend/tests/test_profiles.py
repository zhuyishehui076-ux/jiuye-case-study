"""T5 M3 学生画像测试（SPEC §6.3）。"""

from __future__ import annotations

import json
import random
import re
from uuid import uuid4

from sqlalchemy import func, select

from tests.conftest import login

_EVIDENCE_REF_RE = re.compile(r"\[E\d+\]")


def _evidence_payload(user_prompt: str) -> dict:
    payload, _end = json.JSONDecoder().raw_decode(user_prompt[user_prompt.index("{") :])
    return payload


def _ids_by_type(user_prompt: str) -> dict[str, list[str]]:
    payload = _evidence_payload(user_prompt)
    grouped: dict[str, list[str]] = {}
    for source in payload["evidence"]:
        grouped.setdefault(source["type"], []).append(source["id"])
    return grouped


def _first_id(user_prompt: str, *source_types: str) -> str:
    grouped = _ids_by_type(user_prompt)
    for source_type in source_types:
        ids = grouped.get(source_type, [])
        if ids:
            return ids[0]
    raise AssertionError(f"missing evidence type: {source_types}")


def _refs(text: str) -> list[str]:
    return [ref.strip("[]") for ref in _EVIDENCE_REF_RE.findall(text)]


def _assert_refs_match_evidence_types(text: str, sources_by_id: dict[str, str]) -> None:
    refs = _refs(text)
    assert refs
    assert all(ref in sources_by_id for ref in refs)
    rules = (
        (("投递", "补投", "面试", "offer", "求职行为"), {"job_action", "job_action_summary"}),
        (("活动", "出席", "签到", "就业指导", "招聘会", "宣讲"), {"activity", "activity_summary"}),
        (("测评", "MBTI", "霍兰德", "自评"), {"assessment"}),
    )
    for keywords, allowed_types in rules:
        if any(keyword in text for keyword in keywords):
            assert any(sources_by_id[ref] in allowed_types for ref in refs), text


class NarrativeProviderStub:
    name = "stub"

    def complete_json(self, schema, system, user, retries=2):  # noqa: ARG002
        assert "证据编号" in system
        assert "E1" in user
        student_id = _first_id(user, "student")
        assessment_id = _first_id(user, "assessment")
        activity_id = _first_id(user, "activity_summary", "activity")
        job_id = _first_id(user, "job_action_summary", "job_action")
        return {
            "narrative_text": (
                f"[{student_id}]该生专业与去向信息完整，画像可用于跟进。"
                f"[{assessment_id}]测评记录可支撑当前专业成绩判断。"
            ),
            "suggestions": [
                f"[{job_id}]复盘近30天投递反馈并补投3个相关岗位。",
                f"[{activity_id}]参加1场就业活动并补充实践材料。",
                f"[{assessment_id}]围绕测评短板调整表达。",
            ],
        }

    def embed(self, texts):  # noqa: ARG002
        return []


class NarrativeV2PromptProbe:
    name = "stub"

    def __init__(self) -> None:
        self.system = ""
        self.user = ""

    def complete_json(self, schema, system, user, retries=2):  # noqa: ARG002
        self.system = system
        self.user = user
        assert "profile_narrative_v2" in system
        assert "近30天投递" in user
        assert "近30天面试" in user
        assert "活动出席" in user
        assert "测评类型" in user
        assert "专业" in user
        assert "去向状态" in user
        student_id = _first_id(user, "student")
        assessment_id = _first_id(user, "assessment")
        activity_id = _first_id(user, "activity_summary", "activity")
        job_id = _first_id(user, "job_action_summary", "job_action")
        return {
            "narrative_text": (
                f"[{student_id}]该生未落实去向，专业和方向信息已明确。"
                f"[{job_id}]近30天投递和面试偏少，求职主动性需要补强。"
            ),
            "suggestions": [
                f"[{job_id}]本周补投3个专业相关岗位并记录反馈。",
                f"[{assessment_id}]围绕测评短板补充目标表达。",
                f"[{activity_id}]参加1场招聘活动并完成现场记录。",
            ],
        }

    def embed(self, texts):  # noqa: ARG002
        return []


class ThinNarrativeProviderStub:
    name = "stub"

    def complete_json(self, schema, system, user, retries=2):  # noqa: ARG002
        return {
            "narrative_text": "[E1]该生目前处于红色预警状态，需要关注。",
            "suggestions": ["加强帮扶", "继续跟进", "保持联系"],
        }

    def embed(self, texts):  # noqa: ARG002
        return []


class MismatchedEvidenceProviderStub:
    name = "stub"

    def complete_json(self, schema, system, user, retries=2):  # noqa: ARG002
        student_id = _first_id(user, "student")
        assessment_id = _first_id(user, "assessment")
        activity_id = _first_id(user, "activity_summary", "activity")
        return {
            "narrative_text": (
                f"[{student_id}]近30天投递0次、面试0次，求职主动性不足。"
                f"[{activity_id}]活动出席1场，就业信息获取偏少。"
            ),
            "suggestions": [
                f"[{student_id}]本周补投3个专业相关岗位。",
                f"[{activity_id}]参加1场就业指导活动。",
                f"[{assessment_id}]结合测评结果梳理2个目标方向。",
            ],
        }

    def embed(self, texts):  # noqa: ARG002
        return []


class EvidenceAlignedNarrativeProviderStub:
    name = "stub"

    def complete_json(self, schema, system, user, retries=2):  # noqa: ARG002
        student_id = _first_id(user, "student")
        assessment_id = _first_id(user, "assessment")
        activity_id = _first_id(user, "activity_summary", "activity")
        job_id = _first_id(user, "job_action_summary", "job_action")
        return {
            "narrative_text": (
                f"[{student_id}]该生专业与去向状态已纳入画像判断。"
                f"[{job_id}]近30天投递和面试记录显示求职主动性仍需补强。"
                f"[{activity_id}]活动出席记录可用于安排后续就业指导。"
            ),
            "suggestions": [
                f"[{job_id}]本周补投3个专业相关岗位并记录反馈。",
                f"[{activity_id}]参加1场就业指导活动并完成签到。",
                f"[{assessment_id}]结合测评结果梳理2个目标方向。",
            ],
        }

    def embed(self, texts):  # noqa: ARG002
        return []


def _upsert_profile_weights(db, value: dict) -> None:
    from app.models import AppSetting
    from app.models.setting import PROFILE_SCORE_WEIGHTS_KEY

    row = db.execute(
        select(AppSetting).where(AppSetting.key == PROFILE_SCORE_WEIGHTS_KEY)
    ).scalar_one_or_none()
    if row is None:
        row = AppSetting(
            key=PROFILE_SCORE_WEIGHTS_KEY,
            value=value,
            description="test profile weights",
            created_by="test",
        )
        db.add(row)
    else:
        row.value = value
    db.commit()


def test_profile_score_uses_configured_weights_and_evidence(pg_engine):
    from app.db.session import SessionLocal
    from app.models import Student
    from tests.conftest import reset_case_demo_seed
    from app.services.profiles import (
        DEFAULT_PROFILE_SCORE_WEIGHTS,
        PROFILE_DIMENSIONS,
        load_profile_weights,
        score_student_profile,
    )

    with SessionLocal() as s:
        reset_case_demo_seed(s)
        custom = dict(DEFAULT_PROFILE_SCORE_WEIGHTS)
        custom["专业成绩"] = {
            "self_report": 0.75,
            "degree_level": 0.25,
            "risk_inverse": 1.0,
        }
        _upsert_profile_weights(s, custom)
        student_id = s.execute(select(Student.id).order_by(Student.id).limit(1)).scalar_one()
        weights = load_profile_weights(s)
        score = score_student_profile(s, student_id, "2024-2025-1", weights)

    assert set(score.six_dims_json) == set(PROFILE_DIMENSIONS)
    assert weights["专业成绩"] == {"self_report": 0.75, "degree_level": 0.25}
    assert all(0 <= v <= 100 for v in score.six_dims_json.values())
    assert score.evidence_json["sources"]
    assert score.evidence_json["claims"]
    assert all(c["evidence_ids"] for c in score.evidence_json["claims"])
    academic_claim = next(c for c in score.evidence_json["claims"] if c["dimension"] == "专业成绩")
    sources = {
        source["id"]: source
        for source in score.evidence_json["sources"]
        if source["id"] in academic_claim["evidence_ids"]
    }
    source_blob = str(sources)
    assert "risk_level" not in source_blob
    assert "预警" not in source_blob
    assert "去向" not in source_blob


def test_rebuild_profile_is_idempotent_and_keeps_term_versions(pg_engine):
    from app.db.session import SessionLocal
    from app.models import Profile, Student
    from tests.conftest import reset_case_demo_seed
    from app.services.profiles import rebuild_profile_for_student

    with SessionLocal() as s:
        reset_case_demo_seed(s)
        student_id = s.execute(select(Student.id).order_by(Student.id).limit(1)).scalar_one()
        first = rebuild_profile_for_student(
            s,
            student_id,
            term="2024-2025-1",
            actor="test",
            provider=NarrativeProviderStub(),
        )
        s.commit()
        second = rebuild_profile_for_student(
            s,
            student_id,
            term="2024-2025-1",
            actor="test",
            provider=NarrativeProviderStub(),
        )
        s.commit()
        third = rebuild_profile_for_student(
            s,
            student_id,
            term="2024-2025-2",
            actor="test",
            provider=NarrativeProviderStub(),
        )
        s.commit()
        rows = s.execute(
            select(Profile).where(Profile.student_id == student_id).order_by(Profile.term)
        ).scalars().all()
        first_id = first.id
        second_id = second.id
        third_id = third.id
        first_dims = dict(first.six_dims_json)
        second_dims = dict(second.six_dims_json)
        first_narrative = first.narrative_text or ""
        first_suggestions = list(first.evidence_json["suggestions"])
        first_prompt_ver = first.llm_meta["prompt_ver"]

    assert first_id == second_id
    assert first_dims == second_dims
    assert third_id != first_id
    assert [r.term for r in rows] == ["2024-2025-1", "2024-2025-2"]
    assert len(first_narrative) <= 200
    assert len(first_suggestions) == 3
    assert all(suggestion.startswith("[E") for suggestion in first_suggestions)
    assert any("投递" in suggestion for suggestion in first_suggestions)
    assert any("活动" in suggestion for suggestion in first_suggestions)
    assert any("测评" in suggestion for suggestion in first_suggestions)
    assert first_prompt_ver == "profile_narrative_v2"


def test_profile_narrative_v2_prompt_injects_concrete_facts(pg_engine):
    from app.db.session import SessionLocal
    from app.models import Student
    from tests.conftest import reset_case_demo_seed
    from app.services.profiles import rebuild_profile_for_student

    probe = NarrativeV2PromptProbe()
    with SessionLocal() as s:
        reset_case_demo_seed(s)
        student_id = s.execute(select(Student.id).order_by(Student.id).limit(1)).scalar_one()
        profile = rebuild_profile_for_student(
            s,
            student_id,
            term="2024-2025-1",
            actor="test",
            provider=probe,
        )
        s.commit()
        prompt_ver = profile.llm_meta["prompt_ver"]

    assert prompt_ver == "profile_narrative_v2"
    assert "禁止只复述预警等级" in probe.system
    assert "evidence_facts" in probe.user
    assert "citation_rules" in probe.user


def test_profile_narrative_v2_requeues_thin_output(pg_engine):
    from app.db.session import SessionLocal
    from app.models import Student
    from tests.conftest import reset_case_demo_seed
    from app.services.profiles import rebuild_profile_for_student

    with SessionLocal() as s:
        reset_case_demo_seed(s)
        student_id = s.execute(select(Student.id).order_by(Student.id).limit(1)).scalar_one()
        profile = rebuild_profile_for_student(
            s,
            student_id,
            term="2024-2025-1",
            actor="test",
            provider=ThinNarrativeProviderStub(),
        )
        s.commit()
        llm_meta = dict(profile.llm_meta)

    assert llm_meta["prompt_ver"] == "profile_narrative_v2"
    assert llm_meta["status"] == "failed_queued"
    assert "质量不足" in llm_meta["error"]


def test_profile_narrative_v2_requeues_mismatched_evidence_refs(pg_engine):
    from app.db.session import SessionLocal
    from app.models import Student
    from tests.conftest import reset_case_demo_seed
    from app.services.profiles import rebuild_profile_for_student

    with SessionLocal() as s:
        reset_case_demo_seed(s)
        student_id = s.execute(select(Student.id).order_by(Student.id).limit(1)).scalar_one()
        profile = rebuild_profile_for_student(
            s,
            student_id,
            term="2024-2025-1",
            actor="test",
            provider=MismatchedEvidenceProviderStub(),
        )
        s.commit()
        llm_meta = dict(profile.llm_meta)

    assert llm_meta["prompt_ver"] == "profile_narrative_v2"
    assert llm_meta["status"] == "failed_queued"
    assert "证据编号类型错配" in llm_meta["error"]


def test_profile_narrative_v2_random_20_refs_exist_and_match_types(pg_engine):
    from app.db.session import SessionLocal
    from app.models import Student
    from tests.conftest import reset_case_demo_seed
    from app.services.profiles import rebuild_profile_for_student

    checked: list[dict] = []
    with SessionLocal() as s:
        reset_case_demo_seed(s)
        all_student_ids = s.execute(select(Student.id).order_by(Student.id)).scalars().all()
        sample_ids = random.Random(20260708).sample(all_student_ids, 20)
        for student_id in sample_ids:
            profile = rebuild_profile_for_student(
                s,
                student_id,
                term="2024-2025-1",
                actor="test",
                provider=EvidenceAlignedNarrativeProviderStub(),
            )
            s.commit()
            checked.append(
                {
                    "narrative_text": profile.narrative_text,
                    "suggestions": list(profile.evidence_json["suggestions"]),
                    "sources_by_id": {
                        source["id"]: source["type"]
                        for source in profile.evidence_json["sources"]
                    },
                }
            )

    assert len(checked) == 20
    for profile in checked:
        sources_by_id = profile["sources_by_id"]
        _assert_refs_match_evidence_types(profile["narrative_text"], sources_by_id)
        for suggestion in profile["suggestions"]:
            _assert_refs_match_evidence_types(suggestion, sources_by_id)


def test_profile_rebuild_endpoint_and_counselor_scope(client, pg_engine, demo_passwords):
    from app.db.session import SessionLocal
    from app.models import Profile
    from tests.conftest import reset_case_demo_seed
    from app.seed.users import seed_demo_users

    with SessionLocal() as s:
        reset_case_demo_seed(s)
        seed_demo_users(s)

    office_token = login(client, "office", demo_passwords['office'])
    counselor_token = login(client, "counselor", demo_passwords['counselor'])
    office_headers = {"Authorization": f"Bearer {office_token}"}
    counselor_headers = {"Authorization": f"Bearer {counselor_token}"}

    rebuilt = client.post(
        "/api/v1/profiles/rebuild?term=2024-2025-1",
        headers={**office_headers, "Idempotency-Key": str(uuid4())},
    )
    assert rebuilt.status_code == 200, rebuilt.text
    assert rebuilt.json()["rebuilt"] == 2100

    with SessionLocal() as s:
        assert s.execute(select(func.count()).select_from(Profile)).scalar_one() == 2100

    office_rows = client.get("/api/v1/profiles/students", headers=office_headers).json()
    counselor_rows = client.get("/api/v1/profiles/students", headers=counselor_headers).json()
    assert len(office_rows) == 2100
    assert counselor_rows
    assert all(row["college_id"] == 1 for row in counselor_rows)
    outside = next(row for row in office_rows if row["college_id"] != 1)
    denied = client.get(
        f"/api/v1/profiles/students/{outside['student_id']}",
        headers=counselor_headers,
    )
    assert denied.status_code == 403

    detail = client.get(
        f"/api/v1/profiles/students/{office_rows[0]['student_id']}",
        headers=office_headers,
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["six_dims"]
    assert body["narrative_text"]
    assert body["evidence"]["sources"]
    assert "2024-2025-1" in body["terms"]
