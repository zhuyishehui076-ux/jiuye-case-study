"""种子生成器集成测试（真实 PostgreSQL）——SPEC §7 T1 DONE。

用 reset=True 自清后重建，断言各表达标、risk 三级分布、联系方式密文落库、二跑拒绝。
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.crypto import decrypt_field
from app.models import College, Student
from app.models.enums import RiskLevel
from app.seed.demo import SeedExists, run_demo_seed


@pytest.fixture(scope="module")
def seeded(pg_engine):
    from app.db.session import SessionLocal

    with SessionLocal() as s:
        counts = run_demo_seed(s, reset=True)
    return counts


def test_seed_counts_meet_targets(seeded):
    assert seeded["students"] >= 2000
    assert seeded["colleges"] == 7
    assert seeded["jobs"] == 300
    assert seeded["activities"] == 20
    assert seeded["job_actions"] > 0
    assert seeded["assessments"] > 0
    assert seeded["activity_attendance"] > 0
    assert seeded["fair_checkins"] > 0
    assert seeded["scene_metrics"] > 0
    assert seeded["company_visits"] > 0
    assert seeded["demo_users"] >= 0  # 幂等：已存在则为 0


def test_risk_distribution_has_all_levels(seeded):
    total = seeded["risk_red"] + seeded["risk_yellow"] + seeded["risk_green"]
    assert total == seeded["students"]
    assert seeded["risk_red"] > 0
    assert seeded["risk_yellow"] > 0
    assert seeded["risk_green"] > 0


def test_db_counts_match(pg_engine, seeded):
    from app.db.session import SessionLocal

    with SessionLocal() as s:
        assert s.execute(select(func.count()).select_from(Student)).scalar_one() == seeded["students"]
        assert s.execute(select(func.count()).select_from(College)).scalar_one() == 7
        # risk_level 落库分布与返回一致
        red = s.execute(
            select(func.count()).select_from(Student).where(Student.risk_level == RiskLevel.RED.value)
        ).scalar_one()
        assert red == seeded["risk_red"]


def test_contact_stored_encrypted(pg_engine, seeded):
    from app.db.session import SessionLocal

    with SessionLocal() as s:
        st = s.execute(select(Student).limit(1)).scalar_one()
        assert st.contact_encrypted is not None
        # 落库为密文（非 138 开头明文），可解密回明文手机号
        assert not st.contact_encrypted.startswith("138")
        plain = decrypt_field(st.contact_encrypted)
        assert plain is not None and plain.startswith("138") and len(plain) == 11


def test_second_run_refused(pg_engine, seeded):
    from app.db.session import SessionLocal

    with SessionLocal() as s:
        with pytest.raises(SeedExists):
            run_demo_seed(s, reset=False)
