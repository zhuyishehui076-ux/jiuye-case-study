"""模型约束测试（真实 PostgreSQL；事务回滚隔离）——SPEC §4。"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.crypto import decrypt_field, encrypt_field
from app.models import (
    College,
    FairCheckin,
    Major,
    Profile,
    ResearchDirection,
    Student,
)
from app.models.enums import DegreeLevel
from app.models.org import EMBED_DIM


def _college(db, name="临时学院") -> College:
    c = College(name=name, created_by="t")
    db.add(c)
    db.flush()
    return c


def _major(db, college, code="080901", level=DegreeLevel.BACHELOR.value) -> Major:
    m = Major(code=code, name="计算机科学与技术", level=level, college_id=college.id, created_by="t")
    db.add(m)
    db.flush()
    return m


def _student(db, college, major, no="2025010001") -> Student:
    st = Student(
        student_no=no, name="张三", college_id=college.id, major_id=major.id,
        degree_level=DegreeLevel.BACHELOR.value, cohort_year=2025, created_by="t",
    )
    db.add(st)
    db.flush()
    return st


def test_student_no_unique(db):
    c = _college(db)
    m = _major(db, c)
    _student(db, c, m, no="DUP001")  # 首个已 flush
    # 第二个同学号：不经 helper 内部 flush，统一在断言处触发
    db.add(Student(
        student_no="DUP001", name="李四", college_id=c.id, major_id=m.id,
        degree_level=DegreeLevel.BACHELOR.value, cohort_year=2025, created_by="t"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_major_college_code_level_unique(db):
    # 裁决#2：唯一键 (college_id, code, level)——同学院同代码同层次重复应拒
    c = _college(db)
    db.add(Major(code="080901", name="计算机科学与技术",
                 level=DegreeLevel.BACHELOR.value, college_id=c.id, created_by="t"))
    db.add(Major(code="080901", name="软件工程",
                 level=DegreeLevel.BACHELOR.value, college_id=c.id, created_by="t"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_major_same_code_different_college_allowed(db):
    # 裁决#2：同一专业代码可在不同学院开设
    c1 = _college(db, name="学院甲")
    c2 = _college(db, name="学院乙")
    db.add(Major(code="080901", name="计算机科学与技术",
                 level=DegreeLevel.BACHELOR.value, college_id=c1.id, created_by="t"))
    db.add(Major(code="080901", name="计算机科学与技术",
                 level=DegreeLevel.BACHELOR.value, college_id=c2.id, created_by="t"))
    db.flush()  # 不应抛错
    assert True


def test_profile_student_term_unique(db):
    c = _college(db)
    m = _major(db, c)
    st = _student(db, c, m)
    db.add(Profile(student_id=st.id, term="2024-2025-1", created_by="t"))
    db.add(Profile(student_id=st.id, term="2024-2025-1", created_by="t"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_student_requires_valid_college_fk(db):
    c = _college(db)
    m = _major(db, c)
    db.add(Student(
        student_no="BADFK1", name="李四", college_id=999999, major_id=m.id,
        degree_level=DegreeLevel.BACHELOR.value, cohort_year=2025, created_by="t"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_vector_column_roundtrip(db):
    c = _college(db)
    vec = [0.01 * i for i in range(EMBED_DIM)]
    d = ResearchDirection(name="机器学习", keywords=["深度学习"], embedding=vec,
                          college_id=c.id, created_by="t")
    db.add(d)
    db.flush()
    db.expire(d)
    loaded = db.execute(
        select(ResearchDirection).where(ResearchDirection.id == d.id)
    ).scalar_one()
    assert len(list(loaded.embedding)) == EMBED_DIM


def test_fair_checkin_allows_anonymous(db):
    from datetime import UTC, datetime

    from app.models import Fair

    f = Fair(name="测试招聘会", created_by="t")
    db.add(f)
    db.flush()
    # 匿名计数行：student_id 为空（SPEC §4 同表不同行）
    ck = FairCheckin(fair_id=f.id, anonymous_count=300, gate_id="G0",
                     at=datetime.now(UTC), created_by="t")
    db.add(ck)
    db.flush()
    assert ck.id is not None and ck.student_id is None


def test_contact_encryption_roundtrip():
    plain = "SYNTHETIC-CONTACT-001"
    ct = encrypt_field(plain)
    assert ct is not None and ct != plain  # 密文不等于明文
    assert decrypt_field(ct) == plain
    assert encrypt_field(None) is None
