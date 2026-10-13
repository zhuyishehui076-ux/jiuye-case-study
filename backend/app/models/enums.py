"""业务枚举取值（SPEC §4）。

约定：数据库列用 String 存储（迁移简单、可演进），取值以下列 StrEnum 为唯一事实源，
代码/种子/校验统一引用。避免原生 PG enum 类型带来的迁移负担。
"""

from __future__ import annotations

from enum import StrEnum


class DegreeLevel(StrEnum):
    """学历层次（专业与学生共用）。"""

    BACHELOR = "本科"
    MASTER = "硕士"
    DOCTOR = "博士"


class Gender(StrEnum):
    MALE = "男"
    FEMALE = "女"


class EmploymentStatus(StrEnum):
    """去向状态，对齐教育部去向登记口径（SPEC §4 students.status）。"""

    UNSETTLED = "未落实"
    SIGNED = "签约"
    FURTHER_STUDY = "升学"
    ABROAD = "出国"
    MILITARY = "入伍"
    FLEXIBLE = "灵活就业"
    NOT_SEEKING = "暂不就业"


class RiskLevel(StrEnum):
    """预警等级（由 services/risk.py 规则引擎写入）。"""

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class KeyGroup(StrEnum):
    """困难群体标记（SPEC §4 students.is_key_group）。"""

    LOW_INCOME = "低收入"
    DISABLED = "残疾"
    MINORITY_REMOTE = "少数民族边远"
    LONG_UNEMPLOYED = "长期未就业"


class AssessmentType(StrEnum):
    HOLLAND = "霍兰德"
    MBTI = "MBTI"
    SELF_REPORT = "自评问卷"


class AttendanceSource(StrEnum):
    SCAN = "扫码"
    GATE = "闸机"
    IMPORT = "导入"


class JobActionType(StrEnum):
    APPLY = "投递"
    INTERVIEW = "面试"
    OFFER = "offer"
    ACCEPTED = "录用"
    SIGN = "签约"
    TERMINATE = "解约"


class JobSource(StrEnum):
    FORM = "表单"
    IMPORT = "导入"
    EMAIL = "邮件粘贴"


class AssistStatus(StrEnum):
    OPEN = "进行中"
    CLOSED_EMPLOYED = "已就业关闭"
    TRANSFERRED = "离校转接"


class AssistActionType(StrEnum):
    INTERVIEW = "约谈"
    PUSH_JOB = "推岗"
    TRAINING = "培训"
    VISIT = "走访"
    OTHER = "其他"


class MatchFeedback(StrEnum):
    READ = "学生已读"
    APPLIED = "已投"
    UNSUITABLE = "不合适"


class CheckinSourceScope(StrEnum):
    """招聘会签到行类型：实名 or 匿名计数（SPEC §4 fair_checkins）。"""

    NAMED = "实名"
    ANONYMOUS = "匿名计数"


class FairStatus(StrEnum):
    PLANNED = "计划中"
    REGISTRATION = "报名中"
    CHECKIN = "签到进行中"
    ENDED = "已结束"
    ARCHIVED = "已归档"
