"""预警等级规则引擎（SPEC §4 / §6.1）。

设计约束：`evaluate_risk` 是**纯函数**——输入学生行为聚合 + 阈值，输出等级 + 触发原因，
无 I/O、无全局状态。阈值来自 app_settings 表（load_thresholds），校方可改；
默认值见 DEFAULT_RISK_THRESHOLDS，对齐 SPEC §4：
  红 = 毕业年 5 月后仍未落实，或连续 4 周零求职行为；
  黄 = 投递量低于院系均值 50%。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import EmploymentStatus, RiskLevel

# 仅『未落实』学生纳入预警评估；其余去向（含签约/升学/出国/入伍/灵活/暂不就业）一律绿灯。
WARNING_STATUS = EmploymentStatus.UNSETTLED.value

DEFAULT_RISK_THRESHOLDS: dict[str, float] = {
    "red_unsettled_month": 5,  # 毕业年第几月后仍未落实判红
    "red_zero_action_weeks": 4,  # 连续零求职行为周数判红
    "yellow_apply_ratio": 0.5,  # 投递量 / 院系均值 低于此比例判黄
}


@dataclass(frozen=True)
class RiskInput:
    """学生行为聚合（纯函数入参；由上层从 DB 聚合后构造）。"""

    status: str
    is_graduation_year: bool
    current_month: int  # 1-12
    weeks_since_last_job_action: int | None  # None 表示从无求职行为
    apply_count: int
    college_avg_apply: float


@dataclass(frozen=True)
class RiskResult:
    level: RiskLevel
    reasons: list[str] = field(default_factory=list)


def evaluate_risk(
    data: RiskInput, thresholds: dict[str, float] | None = None
) -> RiskResult:
    """纯函数：聚合 + 阈值 → 等级 + 原因。"""
    t = {**DEFAULT_RISK_THRESHOLDS, **(thresholds or {})}

    # 非『未落实』：不纳入就业预警，直接绿灯
    if data.status != WARNING_STATUS:
        return RiskResult(RiskLevel.GREEN, [])

    red_reasons: list[str] = []
    if (
        data.is_graduation_year
        and data.current_month >= t["red_unsettled_month"]
    ):
        red_reasons.append(
            f"毕业年{int(t['red_unsettled_month'])}月后仍未落实"
        )

    # 连续 N 周零求职行为（含从无行为）
    weeks = data.weeks_since_last_job_action
    if weeks is None or weeks >= t["red_zero_action_weeks"]:
        red_reasons.append(
            f"连续{int(t['red_zero_action_weeks'])}周及以上零求职行为"
        )

    if red_reasons:
        return RiskResult(RiskLevel.RED, red_reasons)

    # 黄灯：投递量低于院系均值指定比例
    if (
        data.college_avg_apply > 0
        and data.apply_count < data.college_avg_apply * t["yellow_apply_ratio"]
    ):
        return RiskResult(
            RiskLevel.YELLOW,
            [
                f"投递量({data.apply_count})低于院系均值"
                f"({data.college_avg_apply:.1f})的{t['yellow_apply_ratio']:.0%}"
            ],
        )

    return RiskResult(RiskLevel.GREEN, [])
