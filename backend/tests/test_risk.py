"""risk 规则引擎纯函数测试（SPEC §4 / §6.1）——无 DB 依赖。"""

from __future__ import annotations

from app.models.enums import EmploymentStatus, RiskLevel
from app.services.risk import (
    DEFAULT_RISK_THRESHOLDS,
    RiskInput,
    evaluate_risk,
)


def _inp(**kw) -> RiskInput:
    base = dict(
        status=EmploymentStatus.UNSETTLED.value,
        is_graduation_year=True,
        current_month=4,
        weeks_since_last_job_action=1,
        apply_count=10,
        college_avg_apply=10.0,
    )
    base.update(kw)
    return RiskInput(**base)


def test_settled_statuses_are_green():
    for status in [
        EmploymentStatus.SIGNED.value,
        EmploymentStatus.FURTHER_STUDY.value,
        EmploymentStatus.ABROAD.value,
        EmploymentStatus.MILITARY.value,
        EmploymentStatus.FLEXIBLE.value,
        EmploymentStatus.NOT_SEEKING.value,
    ]:
        r = evaluate_risk(_inp(status=status, weeks_since_last_job_action=None))
        assert r.level is RiskLevel.GREEN
        assert r.reasons == []


def test_unsettled_no_action_is_red():
    r = evaluate_risk(_inp(weeks_since_last_job_action=None))
    assert r.level is RiskLevel.RED
    assert any("零求职行为" in x for x in r.reasons)


def test_unsettled_stale_action_is_red():
    r = evaluate_risk(_inp(weeks_since_last_job_action=5))
    assert r.level is RiskLevel.RED


def test_graduation_month_rule_is_red():
    # 毕业年 5 月后仍未落实 → 红（即使近期有行为）
    r = evaluate_risk(_inp(current_month=6, weeks_since_last_job_action=0))
    assert r.level is RiskLevel.RED
    assert any("未落实" in x for x in r.reasons)


def test_unsettled_low_apply_is_yellow():
    r = evaluate_risk(_inp(weeks_since_last_job_action=1, apply_count=2, college_avg_apply=10.0))
    assert r.level is RiskLevel.YELLOW
    assert any("投递量" in x for x in r.reasons)


def test_unsettled_active_enough_is_green():
    r = evaluate_risk(_inp(weeks_since_last_job_action=1, apply_count=8, college_avg_apply=10.0))
    assert r.level is RiskLevel.GREEN


def test_threshold_override_changes_outcome():
    # 放宽零行为周数阈值到 8 周后，5 周不再判红；投递充分 → 绿
    r = evaluate_risk(
        _inp(weeks_since_last_job_action=5, apply_count=8, college_avg_apply=10.0),
        thresholds={"red_zero_action_weeks": 8},
    )
    assert r.level is RiskLevel.GREEN


def test_defaults_align_with_spec():
    assert DEFAULT_RISK_THRESHOLDS["red_unsettled_month"] == 5
    assert DEFAULT_RISK_THRESHOLDS["red_zero_action_weeks"] == 4
    assert DEFAULT_RISK_THRESHOLDS["yellow_apply_ratio"] == 0.5
