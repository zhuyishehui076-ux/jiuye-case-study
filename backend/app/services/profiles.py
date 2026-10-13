"""T5 M3 学生画像：确定性六维打分 + LLM 研判编排。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.audit import record_db
from app.llm.prompts.profile_narrative import (
    PROFILE_NARRATIVE_PROMPT_VER,
    PROFILE_NARRATIVE_SYSTEM,
    build_profile_narrative_user_prompt,
)
from app.llm.provider import LLMError, Provider
from app.llm.schemas import PROFILE_NARRATIVE_SCHEMA
from app.models import (
    Activity,
    ActivityAttendance,
    AppSetting,
    Assessment,
    AssistCase,
    AssistRecord,
    JobAction,
    Profile,
    Student,
)
from app.models.enums import (
    AssessmentType,
    EmploymentStatus,
    JobActionType,
)
from app.models.setting import PROFILE_SCORE_WEIGHTS_KEY
from app.services.student_feedback import match_feedback_by_student

JourneyStage = Literal["发现", "帮扶", "推岗", "反馈", "结果"]
JOURNEY_STAGES: tuple[JourneyStage, ...] = ("发现", "帮扶", "推岗", "反馈", "结果")


def derive_journey_stage(
    employment_status: str,
    assist_record_count: int,
    pushed_matches: list[dict[str, Any]],
) -> tuple[JourneyStage, int]:
    """从唯一业务事实推导闭环阶段，供所有展示层复用。"""
    if employment_status != EmploymentStatus.UNSETTLED.value:
        index = 4
    elif any(item.get("feedback") is not None for item in pushed_matches):
        index = 3
    elif pushed_matches:
        index = 2
    elif assist_record_count > 0:
        index = 1
    else:
        index = 0
    return JOURNEY_STAGES[index], index


def build_student_fact_chain(db: Session, student: Student) -> dict[str, Any]:
    """读取同一 student_id 的实时业务事实，画像 evidence_json 只保留为历史快照。"""
    cases = list(
        db.execute(
            select(AssistCase)
            .where(AssistCase.student_id == student.id)
            .order_by(AssistCase.opened_at, AssistCase.id)
        ).scalars()
    )
    case_by_id = {case.id: case for case in cases}
    case_ids = list(case_by_id)
    records = (
        list(
            db.execute(
                select(AssistRecord)
                .where(AssistRecord.case_id.in_(case_ids))
                .order_by(AssistRecord.occurred_at, AssistRecord.id)
            ).scalars()
        )
        if case_ids
        else []
    )
    job_actions = list(
        db.execute(
            select(JobAction)
            .where(JobAction.student_id == student.id)
            .order_by(JobAction.occurred_at, JobAction.id)
        ).scalars()
    )
    pushed_matches = match_feedback_by_student(db, [student.id]).get(student.id, [])

    events: list[dict[str, Any]] = []
    for case in cases:
        events.append(
            {
                "student_id": student.id,
                "event_type": "assist_case_opened",
                "source_type": "assist_case",
                "source_id": case.id,
                "occurred_at": case.opened_at,
                "summary": "建立一生一策帮扶档案",
                "facts": {
                    "opened_reason": case.opened_reason,
                    "assigned_to": case.assigned_to,
                    "current_status": case.status,
                },
            }
        )
    for record in records:
        case = case_by_id[record.case_id]
        events.append(
            {
                "student_id": student.id,
                "event_type": "assist_recorded",
                "source_type": "assist_record",
                "source_id": record.id,
                "occurred_at": record.occurred_at,
                "summary": f"记录帮扶动作：{record.action_type}",
                "facts": {
                    "case_id": case.id,
                    "case_status": case.status,
                    "action_type": record.action_type,
                    "note": record.note,
                    "actor": record.actor,
                },
            }
        )
    for action in job_actions:
        events.append(
            {
                "student_id": student.id,
                "event_type": "job_action",
                "source_type": "job_action",
                "source_id": action.id,
                "occurred_at": action.occurred_at,
                "summary": f"求职动作：{action.type}",
                "facts": {
                    "action_type": action.type,
                    "target": action.target,
                    "source": action.source,
                },
            }
        )
    for match in pushed_matches:
        common_facts = {
            "job_id": match["job_id"],
            "company": match["company"],
            "title": match["title"],
        }
        events.append(
            {
                "student_id": student.id,
                "event_type": "job_pushed",
                "source_type": "match",
                "source_id": match["match_id"],
                "occurred_at": match["pushed_at"],
                "summary": f"推送岗位：{match['company']} · {match['title']}",
                "facts": common_facts,
            }
        )
        if match.get("feedback") is not None:
            events.append(
                {
                    "student_id": student.id,
                    "event_type": "job_feedback",
                    "source_type": "match",
                    "source_id": match["match_id"],
                    "occurred_at": match["feedback_at"],
                    "summary": f"岗位反馈：{match['feedback']}",
                    "facts": {**common_facts, "feedback": match["feedback"]},
                }
            )

    if student.status_occurred_at is not None:
        events.append(
            {
                "student_id": student.id,
                "event_type": "outcome_confirmed",
                "source_type": "student",
                "source_id": student.id,
                "occurred_at": student.status_occurred_at,
                "summary": f"当前去向：{student.status}",
                "facts": {
                    "status": student.status,
                    "risk_level": student.risk_level,
                    "risk_reason": student.risk_reason,
                },
            }
        )
    event_order = {
        "assist_case_opened": 0,
        "assist_recorded": 1,
        "job_action": 2,
        "job_pushed": 3,
        "job_feedback": 4,
        "outcome_confirmed": 5,
    }
    events.sort(
        key=lambda event: (
            event["occurred_at"] is None,
            event["occurred_at"].isoformat() if event["occurred_at"] is not None else "",
            event_order[event["event_type"]],
            event["source_id"],
        )
    )
    return {
        "student_id": student.id,
        "generated_at": datetime.now(UTC),
        "current_outcome": {
            "student_id": student.id,
            "source_type": "student",
            "source_id": student.id,
            "status": student.status,
            "occurred_at": student.status_occurred_at,
            "risk_level": student.risk_level,
            "risk_reason": student.risk_reason,
        },
        "events": events,
    }

PROFILE_DIMENSIONS = (
    "专业成绩",
    "沟通表达",
    "实践能力",
    "求职主动性",
    "科研竞赛",
    "职业清晰度",
)

DEFAULT_PROFILE_SCORE_WEIGHTS: dict[str, dict[str, float]] = {
    "专业成绩": {"self_report": 0.75, "degree_level": 0.25},
    "沟通表达": {"assessment": 0.6, "activity": 0.4},
    "实践能力": {"activity": 0.5, "job_action": 0.5},
    "求职主动性": {"apply_count": 0.5, "recent_action": 0.3, "interview_count": 0.2},
    "科研竞赛": {"activity": 0.6, "self_report": 0.4},
    "职业清晰度": {"assessment": 0.5, "status": 0.3, "direction": 0.2},
}

_QUEUE_NARRATIVE = "成长建议正在准备中，学生的成长与求职依据已整理完成。"
_QUEUE_SUGGESTIONS = [
    "补充求职方向与意向地点",
    "记录最近一次投递或面试进展",
    "参加一场与目标岗位相关的就业活动",
]
_EVIDENCE_REF_RE = re.compile(r"\[E\d+\]")
_RISK_LABEL_RE = re.compile(r"(?<![A-Za-z0-9_])(red|yellow|green)(?![A-Za-z0-9_])", flags=re.IGNORECASE)
_RISK_LABELS = {"red": "红", "yellow": "黄", "green": "绿"}
_EVIDENCE_TYPE_RULES: tuple[tuple[str, tuple[str, ...], set[str]], ...] = (
    (
        "求职行为",
        ("投递", "补投", "面试", "offer", "求职行为"),
        {"job_action", "job_action_summary"},
    ),
    (
        "活动签到",
        ("活动", "出席", "签到", "就业指导", "招聘会", "宣讲"),
        {"activity", "activity_summary"},
    ),
    ("测评", ("测评", "MBTI", "霍兰德", "自评"), {"assessment"}),
)


def _normalize_risk_labels(text: str) -> str:
    return _RISK_LABEL_RE.sub(lambda m: _RISK_LABELS[m.group(1).lower()], text)


@dataclass(frozen=True)
class ProfileScoreResult:
    six_dims_json: dict[str, float]
    evidence_json: dict[str, Any]


class EvidenceBuilder:
    def __init__(self) -> None:
        self.sources: list[dict[str, Any]] = []
        self.claims: list[dict[str, Any]] = []
        self._source_seq = 0
        self._claim_seq = 0

    def source(
        self,
        *,
        source_type: str,
        summary: str,
        object_id: int | None = None,
        value: Any = None,
    ) -> str:
        self._source_seq += 1
        evidence_id = f"E{self._source_seq}"
        row: dict[str, Any] = {
            "id": evidence_id,
            "type": source_type,
            "summary": summary,
        }
        if object_id is not None:
            row["object_id"] = object_id
        if value is not None:
            row["value"] = value
        self.sources.append(row)
        return evidence_id

    def claim(self, *, dimension: str, score: float, evidence_ids: list[str]) -> None:
        self._claim_seq += 1
        self.claims.append(
            {
                "id": f"C{self._claim_seq}",
                "dimension": dimension,
                "text": f"{dimension}={score:.1f}",
                "evidence_ids": evidence_ids,
            }
        )

    def to_json(self) -> dict[str, Any]:
        return {"sources": self.sources, "claims": self.claims, "suggestions": []}


def load_profile_weights(db: Session) -> dict[str, dict[str, float]]:
    row = db.execute(
        select(AppSetting).where(AppSetting.key == PROFILE_SCORE_WEIGHTS_KEY)
    ).scalar_one_or_none()
    raw = row.value if row is not None and isinstance(row.value, dict) else {}
    weights: dict[str, dict[str, float]] = {}
    for dim, defaults in DEFAULT_PROFILE_SCORE_WEIGHTS.items():
        override = raw.get(dim)
        if isinstance(override, dict) and override:
            weights[dim] = {
                key: float(override.get(key, default_value))
                for key, default_value in defaults.items()
            }
        else:
            weights[dim] = dict(defaults)
    return weights


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _avg(values: list[float], default: float) -> float:
    return sum(values) / len(values) if values else default


def _score_count(count: int, target: int) -> float:
    return _clamp((count / target) * 100.0 if target else 0.0)


def _weighted_score(signals: dict[str, tuple[float, list[str]]], weights: dict[str, float]) -> tuple[float, list[str]]:
    total = sum(max(0.0, w) for w in weights.values())
    if total <= 0:
        return 0.0, []
    score = 0.0
    evidence_ids: list[str] = []
    for key, weight in weights.items():
        if key not in signals:
            continue
        value, ids = signals[key]
        score += value * max(0.0, weight)
        for eid in ids:
            if eid not in evidence_ids:
                evidence_ids.append(eid)
    return round(_clamp(score / total), 1), evidence_ids


def _answers_score(assessment: Assessment | None, keys: tuple[str, ...], default: float) -> float:
    if assessment is None:
        return default
    answers = assessment.result_json.get("answers", {})
    values = []
    for key in keys:
        raw = answers.get(key)
        if isinstance(raw, int | float):
            values.append(_clamp(float(raw), 1.0, 5.0))
    return _avg([v / 5.0 * 100.0 for v in values], default)


def _degree_score(level: str) -> float:
    return {"本科": 75.0, "硕士": 85.0, "博士": 95.0}.get(level, 70.0)


def _status_score(status: str) -> float:
    if status in {
        EmploymentStatus.SIGNED.value,
        EmploymentStatus.FURTHER_STUDY.value,
        EmploymentStatus.ABROAD.value,
        EmploymentStatus.MILITARY.value,
    }:
        return 90.0
    if status == EmploymentStatus.FLEXIBLE.value:
        return 78.0
    if status == EmploymentStatus.NOT_SEEKING.value:
        return 62.0
    return 48.0


def _recent_score(days_since: int | None) -> float:
    if days_since is None:
        return 20.0
    if days_since <= 14:
        return 100.0
    if days_since <= 28:
        return 80.0
    if days_since <= 60:
        return 55.0
    return 30.0


def _latest_assessment(assessments: list[Assessment], atype: str) -> Assessment | None:
    for row in sorted(assessments, key=lambda a: a.id, reverse=True):
        if row.type == atype:
            return row
    return None


def score_student_profile(
    db: Session,
    student_id: int,
    term: str,
    weights: dict[str, dict[str, float]] | None = None,
) -> ProfileScoreResult:
    student = db.execute(
        select(Student)
        .options(
            joinedload(Student.college),
            joinedload(Student.major),
            joinedload(Student.direction),
        )
        .where(Student.id == student_id)
    ).scalar_one_or_none()
    if student is None:
        raise ValueError("student not found")

    weights = weights or load_profile_weights(db)
    evidence = EvidenceBuilder()
    student_eid = evidence.source(
        source_type="student",
        object_id=student.id,
        summary=(
            f"{student.name}，{student.college.name}，{student.major.name}，"
            f"去向={student.status}，预警={student.risk_level}"
        ),
        value={
            "status": student.status,
            "risk_level": student.risk_level,
            "degree_level": student.degree_level,
        },
    )
    degree_eid = evidence.source(
        source_type="student_degree",
        object_id=student.id,
        summary=f"学历层次：{student.degree_level}",
        value={"degree_level": student.degree_level},
    )

    assessments = db.execute(
        select(Assessment)
        .where(Assessment.student_id == student_id)
        .where(Assessment.term == term)
        .order_by(Assessment.id)
    ).scalars().all()
    if not assessments:
        assessments = db.execute(
            select(Assessment).where(Assessment.student_id == student_id).order_by(Assessment.id)
        ).scalars().all()
    self_report = _latest_assessment(assessments, AssessmentType.SELF_REPORT.value)
    holland = _latest_assessment(assessments, AssessmentType.HOLLAND.value)
    mbti = _latest_assessment(assessments, AssessmentType.MBTI.value)
    assessment_eids: list[str] = []
    for assessment in assessments:
        assessment_eids.append(
            evidence.source(
                source_type="assessment",
                object_id=assessment.id,
                summary=f"{assessment.term} {assessment.type} 测评",
                value=assessment.result_json,
            )
        )
    if not assessment_eids:
        assessment_eids.append(
            evidence.source(
                source_type="assessment",
                summary="本学期暂无测评记录，按缺省值处理",
            )
        )

    assessment_facts = [
        {
            "证据编号": eid,
            "测评类型": assessment.type,
            "学期": assessment.term,
            "测评结果": assessment.result_json,
        }
        for eid, assessment in zip(assessment_eids, assessments, strict=False)
    ]
    if not assessment_facts:
        assessment_facts = [
            {
                "证据编号": assessment_eids[0],
                "测评类型": "暂无",
                "学期": term,
                "测评结果": "本学期暂无测评记录，按缺省值处理",
            }
        ]

    attendance_rows = db.execute(
        select(ActivityAttendance, Activity)
        .join(Activity, ActivityAttendance.activity_id == Activity.id)
        .where(ActivityAttendance.student_id == student_id)
        .order_by(ActivityAttendance.checked_at)
    ).all()
    activity_count = len(attendance_rows)
    activity_names = [activity.name for _att, activity in attendance_rows[:5]]
    activity_eids = [
        evidence.source(
            source_type="activity",
            object_id=activity.id,
            summary=f"参与{activity.category or '活动'}：{activity.name}",
        )
        for _att, activity in attendance_rows[:5]
    ]
    if not activity_eids:
        activity_eids.append(
            evidence.source(source_type="activity", summary="暂无活动签到记录")
        )
    activity_summary_eid = evidence.source(
        source_type="activity_summary",
        summary=f"活动出席汇总：共出席{activity_count}场活动",
        value={"活动出席": activity_count, "样例活动": activity_names},
    )
    activity_eids.append(activity_summary_eid)

    actions = db.execute(
        select(JobAction)
        .where(JobAction.student_id == student_id)
        .order_by(JobAction.occurred_at)
    ).scalars().all()
    apply_count = sum(1 for a in actions if a.type == JobActionType.APPLY.value)
    interview_count = sum(
        1
        for a in actions
        if a.type
        in {
            JobActionType.INTERVIEW.value,
            JobActionType.OFFER.value,
            JobActionType.ACCEPTED.value,
            JobActionType.SIGN.value,
        }
    )
    reference_at = db.execute(select(func.max(JobAction.occurred_at))).scalar_one_or_none()
    latest_action = max((a.occurred_at for a in actions), default=None)
    if reference_at and latest_action:
        days_since = max(0, (reference_at - latest_action).days)
    else:
        days_since = None
    recent_cutoff = reference_at - timedelta(days=30) if reference_at else None
    recent_actions = [
        action for action in actions if recent_cutoff is not None and action.occurred_at >= recent_cutoff
    ]
    recent_apply_count = sum(1 for a in recent_actions if a.type == JobActionType.APPLY.value)
    recent_interview_count = sum(1 for a in recent_actions if a.type == JobActionType.INTERVIEW.value)
    action_eids = [
        evidence.source(
            source_type="job_action",
            object_id=action.id,
            summary=f"{action.type}：{action.target or '未填目标'}",
        )
        for action in actions[-5:]
    ]
    if not action_eids:
        action_eids.append(
            evidence.source(source_type="job_action", summary="暂无求职行为记录")
        )
    action_summary_eid = evidence.source(
        source_type="job_action_summary",
        summary=(
            f"求职行为汇总：近30天投递{recent_apply_count}次、"
            f"近30天面试{recent_interview_count}次；累计投递{apply_count}次、"
            f"累计面试/收到录用/签约{interview_count}次"
        ),
        value={
            "近30天投递": recent_apply_count,
            "近30天面试": recent_interview_count,
            "累计投递": apply_count,
            "累计面试_offer_签约": interview_count,
            "距最近求职动作天数": days_since,
        },
    )
    action_eids.append(action_summary_eid)

    direction_eid = evidence.source(
        source_type="direction",
        object_id=student.direction_id,
        summary=(
            f"研究方向：{student.direction.name}"
            if student.direction is not None
            else "未填研究方向，按专业方向基础值处理"
        ),
        value=getattr(student.direction, "keywords", None) if student.direction is not None else None,
    )

    self_report_academic = _answers_score(self_report, ("q1", "q2", "q3"), 60.0)
    self_report_research = _answers_score(self_report, ("q6", "q7"), 55.0)
    communication_assessment = _answers_score(self_report, ("q4", "q5"), 60.0)
    career_assessment = 82.0 if holland or mbti else 60.0

    signals: dict[str, dict[str, tuple[float, list[str]]]] = {
        "专业成绩": {
            "self_report": (self_report_academic, assessment_eids),
            "degree_level": (_degree_score(student.degree_level), [degree_eid]),
        },
        "沟通表达": {
            "assessment": (communication_assessment, assessment_eids),
            "activity": (_score_count(activity_count, 4), activity_eids),
        },
        "实践能力": {
            "activity": (_score_count(activity_count, 4), activity_eids),
            "job_action": (_score_count(interview_count, 4), action_eids),
        },
        "求职主动性": {
            "apply_count": (_score_count(apply_count, 8), action_eids),
            "recent_action": (_recent_score(days_since), action_eids),
            "interview_count": (_score_count(interview_count, 3), action_eids),
        },
        "科研竞赛": {
            "activity": (_score_count(activity_count, 4), activity_eids),
            "self_report": (self_report_research, assessment_eids),
        },
        "职业清晰度": {
            "assessment": (career_assessment, assessment_eids),
            "status": (_status_score(student.status), [student_eid]),
            "direction": (88.0 if student.direction is not None else 65.0, [direction_eid]),
        },
    }

    six_dims: dict[str, float] = {}
    for dim in PROFILE_DIMENSIONS:
        score, evidence_ids = _weighted_score(signals[dim], weights[dim])
        if not evidence_ids:
            evidence_ids = [student_eid]
        six_dims[dim] = score
        evidence.claim(dimension=dim, score=score, evidence_ids=evidence_ids)

    evidence_json = evidence.to_json()
    evidence_json["facts"] = {
        "学生基本事实": {
            "姓名": student.name,
            "学院": student.college.name,
            "专业": student.major.name,
            "学历": student.degree_level,
            "去向状态": student.status,
            "预警等级": student.risk_level,
            "研究方向": student.direction.name if student.direction is not None else None,
            "证据编号": student_eid,
        },
        "测评事实": assessment_facts,
        "求职行为事实": {
            "证据编号": action_summary_eid,
            "近30天投递": recent_apply_count,
            "近30天面试": recent_interview_count,
            "累计投递": apply_count,
            "累计面试_offer_签约": interview_count,
            "距最近求职动作天数": days_since,
        },
        "活动事实": {
            "证据编号": activity_summary_eid,
            "活动出席": activity_count,
            "样例活动": activity_names,
        },
    }

    return ProfileScoreResult(six_dims_json=six_dims, evidence_json=evidence_json)


def _build_student_payload(student: Student) -> dict[str, Any]:
    return {
        "student_id": student.id,
        "student_no": student.student_no,
        "name": student.name,
        "college": student.college.name if student.college else None,
        "major": student.major.name if student.major else None,
        "status": student.status,
        "risk_level": student.risk_level,
    }


def _queue_narrative(first_evidence_id: str | None) -> tuple[str, list[str], dict[str, Any]]:
    prefix = f"[{first_evidence_id}]" if first_evidence_id else ""
    return (
        f"{prefix}{_QUEUE_NARRATIVE}",
        list(_QUEUE_SUGGESTIONS),
        {
            "provider": "queue",
            "model": None,
            "prompt_ver": PROFILE_NARRATIVE_PROMPT_VER,
            "status": "queued",
        },
    )


def _generate_narrative(
    provider: Provider | None,
    *,
    student: Student,
    six_dims: dict[str, float],
    evidence_json: dict[str, Any],
) -> tuple[str, list[str], dict[str, Any]]:
    sources = evidence_json.get("sources", [])
    first_evidence_id = sources[0]["id"] if sources else None
    if provider is None:
        return _queue_narrative(first_evidence_id)

    try:
        source_types = {str(s["id"]): str(s.get("type", "")) for s in sources}
        base_user_prompt = build_profile_narrative_user_prompt(
            student=_build_student_payload(student),
            six_dims=six_dims,
            evidence_sources=sources,
            evidence_facts=evidence_json.get("facts", {}),
        )
        last_quality_error: Exception | None = None
        narrative = ""
        suggestions: list[str] = []
        for attempt in range(2):
            system_prompt = PROFILE_NARRATIVE_SYSTEM
            user_prompt = base_user_prompt
            if attempt == 1 and last_quality_error is not None:
                system_prompt = (
                    f"{PROFILE_NARRATIVE_SYSTEM}\n"
                    "上一次输出因质量不足被拒收。请严格修正：narrative_text 每句带 [E编号]；"
                    "suggestions 正好3条且每条必须以 [E编号] 开头；投递/面试引用求职行为证据，"
                    "活动/出席引用活动签到证据，测评引用测评证据。"
                )
                user_prompt = (
                    f"{base_user_prompt}\n"
                    f"上次错误：{last_quality_error}。请按 citation_rules.preferred_ids 替换错配编号，"
                    "只输出合格 JSON。"
                )
            data = provider.complete_json(
                PROFILE_NARRATIVE_SCHEMA,
                system_prompt,
                user_prompt,
            )
            narrative = _normalize_risk_labels(str(data["narrative_text"]).strip())
            if len(narrative) > 200:
                narrative = narrative[:200]
            normalized_suggestions = [
                _normalize_risk_labels(str(s).strip()) for s in data["suggestions"]
            ]
            try:
                _validate_narrative_quality(narrative, normalized_suggestions, source_types)
            except LLMError as exc:
                last_quality_error = exc
                if attempt == 1:
                    raise
                continue
            suggestions = normalized_suggestions
            break
        return (
            narrative,
            suggestions,
            {
                "provider": provider.name,
                "model": getattr(provider, "model", None),
                "prompt_ver": PROFILE_NARRATIVE_PROMPT_VER,
                "status": "generated",
            },
        )
    except Exception as exc:
        narrative, suggestions, meta = _queue_narrative(first_evidence_id)
        meta["status"] = "failed_queued"
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return narrative, suggestions, meta


def _validate_narrative_quality(
    narrative: str,
    suggestions_raw: Any,
    source_types: dict[str, str],
) -> None:
    narrative_refs = _extract_evidence_refs(narrative)
    _validate_evidence_refs(narrative, narrative_refs, source_types)
    if len(set(narrative_refs)) < 2:
        raise LLMError("画像研判质量不足：研判句缺少足够证据编号")

    suggestions = [str(s).strip() for s in suggestions_raw]
    if len(suggestions) != 3 or any(not s for s in suggestions):
        raise LLMError("画像研判质量不足：建议必须正好3条且非空")
    for suggestion in suggestions:
        suggestion_refs = _extract_evidence_refs(suggestion)
        _validate_evidence_refs(suggestion, suggestion_refs, source_types)
        if not suggestion_refs:
            raise LLMError("画像研判质量不足：建议缺少证据编号")


def _extract_evidence_refs(text: str) -> list[str]:
    return [ref.strip("[]") for ref in _EVIDENCE_REF_RE.findall(text)]


def _validate_evidence_refs(
    text: str,
    refs: list[str],
    source_types: dict[str, str],
) -> None:
    missing_refs = sorted({ref for ref in refs if ref not in source_types})
    if missing_refs:
        raise LLMError(f"画像研判质量不足：未知证据编号 {', '.join(missing_refs)}")

    for label, keywords, allowed_types in _EVIDENCE_TYPE_RULES:
        if not any(keyword in text for keyword in keywords):
            continue
        cited_types = {source_types[ref] for ref in refs}
        if cited_types.isdisjoint(allowed_types):
            allowed = "/".join(sorted(allowed_types))
            raise LLMError(f"画像研判质量不足：证据编号类型错配（{label}需引用{allowed}）")


def rebuild_profile_for_student(
    db: Session,
    student_id: int,
    *,
    term: str,
    actor: str,
    provider: Provider | None = None,
) -> Profile:
    student = db.execute(
        select(Student)
        .options(
            joinedload(Student.college),
            joinedload(Student.major),
            joinedload(Student.direction),
        )
        .where(Student.id == student_id)
    ).scalar_one_or_none()
    if student is None:
        raise ValueError("student not found")

    score = score_student_profile(db, student_id, term, load_profile_weights(db))
    narrative, suggestions, llm_meta = _generate_narrative(
        provider,
        student=student,
        six_dims=score.six_dims_json,
        evidence_json=score.evidence_json,
    )
    evidence_json = dict(score.evidence_json)
    evidence_json["suggestions"] = suggestions

    profile = db.execute(
        select(Profile).where(Profile.student_id == student_id, Profile.term == term)
    ).scalar_one_or_none()
    before = None
    if profile is None:
        profile = Profile(
            student_id=student_id,
            term=term,
            six_dims_json=score.six_dims_json,
            narrative_text=narrative,
            evidence_json=evidence_json,
            llm_meta=llm_meta,
            created_by=actor,
        )
        db.add(profile)
    else:
        before = {
            "six_dims_json": profile.six_dims_json,
            "llm_meta": profile.llm_meta,
        }
        profile.six_dims_json = score.six_dims_json
        profile.narrative_text = narrative
        profile.evidence_json = evidence_json
        profile.llm_meta = llm_meta
        profile.updated_at = datetime.now(UTC)
    db.flush()
    record_db(
        db,
        actor=actor,
        action="profile.rebuild",
        object_type="profile",
        object_id=profile.id,
        change_summary={
            "student_id": student_id,
            "term": term,
            "before": before,
            "after": {"six_dims_json": profile.six_dims_json, "llm_meta": profile.llm_meta},
        },
    )
    db.flush()
    return profile


def rebuild_profiles(
    db: Session,
    *,
    term: str,
    actor: str,
    provider: Provider | None = None,
    college_id: int | None = None,
    student_ids: list[int] | None = None,
) -> int:
    stmt = select(Student.id).order_by(Student.id)
    if college_id is not None:
        stmt = stmt.where(Student.college_id == college_id)
    if student_ids is not None:
        stmt = stmt.where(Student.id.in_(student_ids))
    selected_ids = db.execute(stmt).scalars().all()
    for student_id in selected_ids:
        rebuild_profile_for_student(
            db,
            student_id,
            term=term,
            actor=actor,
            provider=provider,
        )
    return len(selected_ids)
