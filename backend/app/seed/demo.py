"""演示种子生成器（SPEC §1 演示态要求 / §7 T1）。

`python -m app.seed --demo` 生成一套自洽的虚构数据：
  ≥2000 学生、7 学院、300 岗位、20 活动，配套测评/签到/求职行为/去向，
  并用 services/risk.py 规则引擎回填 risk_level。全部虚构名，绝无真实个人信息。

幂等策略：默认**拒绝二跑**（已存在学生即报错退出）；`--reset` 显式清空业务数据后重建。
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.core.config import require_demo_profile, settings
from app.core.crypto import encrypt_field
from app.models import (
    Activity,
    ActivityAttendance,
    AppSetting,
    Assessment,
    AssistCase,
    AssistRecord,
    College,
    CompanyVisit,
    Fair,
    FairCheckin,
    FairCompany,
    Job,
    JobAction,
    JobParse,
    Major,
    Match,
    ResearchDirection,
    SceneMetric,
    Student,
)
from app.models.enums import (
    AssessmentType,
    AssistActionType,
    AttendanceSource,
    DegreeLevel,
    EmploymentStatus,
    Gender,
    JobActionType,
    JobSource,
    KeyGroup,
    MatchFeedback,
    RiskLevel,
)
from app.models.setting import (
    DASHBOARD_LIGHT_THRESHOLDS_KEY,
    EMPLOYMENT_TARGET_KEY,
    JOB_MATCH_WEIGHTS_KEY,
    PROFILE_SCORE_WEIGHTS_KEY,
    RISK_THRESHOLDS_KEY,
)
from app.seed import names
from app.seed.users import seed_demo_users
from app.services.assist_idempotency import (
    assist_record_request_hash,
    deterministic_assist_idempotency_key,
)
from app.services.installation import (
    DEMO_DATASET_ID,
    DEMO_DATASET_MARKER_KEY,
    INSTALLATION_PROFILE_KEY,
    ensure_installation_profile,
)
from app.services.matching import DEFAULT_MATCH_WEIGHTS
from app.services.profiles import DEFAULT_PROFILE_SCORE_WEIGHTS, rebuild_profile_for_student
from app.services.risk import DEFAULT_RISK_THRESHOLDS, RiskInput, evaluate_risk
from app.services.thresholds import (
    DEFAULT_DASHBOARD_LIGHT_THRESHOLDS,
    DEFAULT_EMPLOYMENT_TARGET_RATE,
    load_risk_thresholds,
)

RNG_SEED = 42
GRAD_YEAR = 2025
# 评估基准日（毕业年 4 月：月份规则未触发，红灯来自零求职行为，从而产生绿/黄/红三级分布）
DEMO_AS_OF_DATE = datetime(GRAD_YEAR, 4, 15, tzinfo=UTC)
AS_OF = DEMO_AS_OF_DATE
N_STUDENTS = 2100
N_JOBS = 300
N_ACTIVITIES = 20
N_FAIRS = 4
DEMO_PREFLIGHT_STATE_KEY = "demo_scenario_preflight_ready"
DEMO_ALLOWED_CREATORS = (
    "seed",
    "s6-story-seed",
    "s6-boundary-seed",
    "d2-seed",
    "demo-scenario",
    "admin",
    "office",
    "counselor",
    "leader",
)
DEMO_ALLOWED_SETTING_KEYS = (
    INSTALLATION_PROFILE_KEY,
    DEMO_DATASET_MARKER_KEY,
    DEMO_PREFLIGHT_STATE_KEY,
    RISK_THRESHOLDS_KEY,
    DASHBOARD_LIGHT_THRESHOLDS_KEY,
    EMPLOYMENT_TARGET_KEY,
    JOB_MATCH_WEIGHTS_KEY,
    PROFILE_SCORE_WEIGHTS_KEY,
)

# 7 学院 → 专业（教育部本科专业目录代码风格）→ 研究方向
COLLEGES: dict[str, dict] = {
    "计算机与人工智能学院": {
        "majors": [("080901", "计算机科学与技术"), ("080902", "软件工程"), ("080910", "数据科学与大数据技术"), ("080717", "人工智能")],
        "directions": [("机器学习", ["深度学习", "神经网络", "模型训练"]), ("大数据", ["分布式", "数据仓库", "实时计算"])],
    },
    "机械工程学院": {
        "majors": [("080201", "机械工程"), ("080202", "机械设计制造及其自动化"), ("080205", "工业设计")],
        "directions": [("智能制造", ["数控", "机器人", "自动化产线"]), ("机械设计", ["结构强度", "CAD", "有限元"])],
    },
    "电子信息学院": {
        "majors": [("080701", "电子信息工程"), ("080703", "通信工程"), ("080714", "电子科学与技术")],
        "directions": [("嵌入式系统", ["单片机", "FPGA", "驱动开发"]), ("通信", ["5G", "信号处理", "射频"])],
    },
    "材料科学与工程学院": {
        "majors": [("080401", "材料科学与工程"), ("080406", "无机非金属材料工程"), ("080407", "高分子材料与工程")],
        "directions": [("新能源材料", ["锂电池", "储能", "电极"]), ("复合材料", ["碳纤维", "涂层", "界面"])],
    },
    "生物与医药学院": {
        "majors": [("071001", "生物科学"), ("083001", "生物工程"), ("100701", "药学")],
        "directions": [("生物制药", ["发酵", "抗体", "纯化"]), ("分子生物", ["基因编辑", "测序", "蛋白"])],
    },
    "经济管理学院": {
        "majors": [("020101", "经济学"), ("120201", "工商管理"), ("120204", "财务管理"), ("120801", "电子商务")],
        "directions": [("数字经济", ["电商", "供应链", "运营"]), ("金融科技", ["风控", "量化", "支付"])],
    },
    "外国语学院": {
        "majors": [("050201", "英语"), ("050207", "日语"), ("050262", "商务英语")],
        "directions": [("语言服务", ["翻译", "本地化", "跨文化"])],
    },
}

# 去向分布（未落实占比足够产生预警样本）
STATUS_WEIGHTS: list[tuple[str, float]] = [
    (EmploymentStatus.SIGNED.value, 0.45),
    (EmploymentStatus.FURTHER_STUDY.value, 0.12),
    (EmploymentStatus.ABROAD.value, 0.03),
    (EmploymentStatus.MILITARY.value, 0.02),
    (EmploymentStatus.FLEXIBLE.value, 0.08),
    (EmploymentStatus.NOT_SEEKING.value, 0.03),
    (EmploymentStatus.UNSETTLED.value, 0.27),
]

JOB_MAJOR_CODES: dict[str, list[str]] = {
    "后端开发工程师": ["080901", "080902"],
    "前端开发工程师": ["080901", "080902"],
    "算法工程师": ["080901", "080717"],
    "大数据开发工程师": ["080910", "080901"],
    "嵌入式软件工程师": ["080701", "080714"],
    "通信工程师": ["080703", "080701"],
    "机械设计工程师": ["080201", "080202"],
    "工业设计师": ["080205"],
    "材料研发工程师": ["080401", "080406"],
    "电池材料工程师": ["080401", "080407"],
    "生物制药研究员": ["083001", "100701"],
    "分子生物研究员": ["071001", "083001"],
    "财务分析师": ["120204"],
    "电商运营专员": ["120801", "120201"],
    "行业研究员": ["020101", "120201"],
    "英语翻译": ["050201", "050262"],
    "日语商务专员": ["050207"],
}

STORY_JOB_FIXTURES = [
    ("航图数据科技（模拟企业）", "就业数据产品助理", ["080901", "080902", "080910"]),
    ("光谷智能装备（模拟企业）", "机械设计助理工程师", ["080201", "080202", "080205"]),
    ("江城新材料（模拟企业）", "材料研发助理", ["080401", "080406", "080407"]),
    ("东湖生物科技（模拟企业）", "生物研发助理", ["071001", "083001", "100701"]),
    ("楚天国际服务（模拟企业）", "国际业务助理", ["050201", "050207", "050262"]),
    ("长江数字产业（模拟企业）", "软件开发工程师", ["080901", "080902", "080910", "080717"]),
]


class SeedExists(RuntimeError):
    """已存在种子数据，拒绝二跑。"""


class SeedSourceMismatch(RuntimeError):
    """检测到非本内置场景创建的业务数据，禁止清空或覆盖。"""


# 业务表清空顺序（尊重外键；不含 users/roles —— 由 seed_demo_users 幂等处理）
_RESET_ORDER = [
    "enterprise_job_submissions", "enterprise_access_tokens",
    "student_consents", "student_bindings",
    "llm_batch_items", "llm_batch_tasks",
    "scene_metrics", "fair_snapshots", "fair_enterprise_checkins",
    "fair_checkins", "fair_companies", "fairs",
    "activity_attendance", "activities", "assessments", "job_actions",
    "matches", "job_parses", "jobs", "assist_records", "assist_cases",
    "company_visits", "profiles", "students",
    "research_directions", "majors", "colleges",
]


def _reset_business_data(db: Session) -> None:
    db.execute(text("TRUNCATE TABLE " + ", ".join(_RESET_ORDER) + " RESTART IDENTITY CASCADE"))
    db.execute(
        delete(AppSetting).where(AppSetting.key != INSTALLATION_PROFILE_KEY)
    )


def _has_business_data(db: Session) -> bool:
    for table in _RESET_ORDER:
        if table == "app_settings":
            continue
        if bool(db.scalar(text(f"SELECT EXISTS (SELECT 1 FROM {table} LIMIT 1)"))):
            return True
    unknown_setting = db.scalar(
        select(func.count())
        .select_from(AppSetting)
        .where(
            AppSetting.key.not_in(
                (DEMO_PREFLIGHT_STATE_KEY, INSTALLATION_PROFILE_KEY)
            )
        )
    )
    if int(unknown_setting or 0) > 0:
        return True
    return False


def has_unknown_business_data(db: Session) -> bool:
    if _has_business_data(db) and not has_demo_dataset_marker(db):
        return True
    allowed = ", ".join(f"'{actor}'" for actor in DEMO_ALLOWED_CREATORS)
    unknown_setting = db.scalar(
        select(func.count())
        .select_from(AppSetting)
        .where(AppSetting.key.not_in(DEMO_ALLOWED_SETTING_KEYS))
    )
    if int(unknown_setting or 0) > 0:
        return True
    for table in _RESET_ORDER:
        if table == "app_settings":
            continue
        query = text(
            f"SELECT EXISTS (SELECT 1 FROM {table} "
            "WHERE created_by IS NULL OR ("
            f"created_by NOT IN ({allowed}) "
            "AND created_by NOT LIKE 'student:%' "
            "AND created_by NOT LIKE 'enterprise:%' "
            "AND created_by NOT LIKE 'credential:demo-%'"
            ") LIMIT 1)"
        )
        if bool(db.scalar(query)):
            return True
    return False


def has_demo_dataset_marker(db: Session) -> bool:
    marker = db.execute(
        select(AppSetting).where(AppSetting.key == DEMO_DATASET_MARKER_KEY)
    ).scalar_one_or_none()
    return bool(marker and marker.value.get("dataset_id") == DEMO_DATASET_ID)


def clear_demo_seed(db: Session, *, commit: bool = True) -> int:
    """将内置演示业务数据整体恢复为空库，保留登录账号。"""
    require_demo_profile("clear_demo_seed")
    ensure_installation_profile(
        db,
        expected_profile=settings.profile,
        expected_installation_id=settings.installation_id,
    )
    existing = int(db.execute(select(func.count()).select_from(Student)).scalar_one())
    if has_unknown_business_data(db):
        raise SeedSourceMismatch("检测到来源不明的业务数据，拒绝按演示场景清空。")
    preflight_exists = db.scalar(
        select(func.count())
        .select_from(AppSetting)
        .where(AppSetting.key == DEMO_PREFLIGHT_STATE_KEY)
    )
    if not _has_business_data(db) and not preflight_exists:
        return 0
    _reset_business_data(db)
    if commit:
        db.commit()
    return existing


def _weighted_status(rng: random.Random) -> str:
    r = rng.random()
    acc = 0.0
    for status, w in STATUS_WEIGHTS:
        acc += w
        if r <= acc:
            return status
    return EmploymentStatus.UNSETTLED.value


def run_demo_seed(
    db: Session,
    *,
    reset: bool = False,
    commit: bool = True,
    enforce_source_guard: bool = True,
) -> dict[str, int]:
    """生成内置模拟场景。

    ``enforce_source_guard=False`` 仅供共享临时 PostgreSQL 测试夹具清场；API、CLI
    与产品运行路径一律使用默认严格保护。
    """
    require_demo_profile("run_demo_seed")
    ensure_installation_profile(
        db,
        expected_profile=settings.profile,
        expected_installation_id=settings.installation_id,
    )
    existing = db.execute(select(func.count()).select_from(Student)).scalar_one()
    if enforce_source_guard and has_unknown_business_data(db):
        raise SeedSourceMismatch("检测到来源不明的业务数据，拒绝按演示场景写入或覆盖。")
    if existing and not reset:
        raise SeedExists(
            f"检测到已有 {existing} 名学生。seed 默认拒绝二跑以防重复写入；"
            "如需重建请显式加 --reset（将 TRUNCATE 全部业务表）。"
        )
    if reset:
        _reset_business_data(db)

    rng = random.Random(RNG_SEED)
    counts: dict[str, int] = {}

    # 1) app_settings：阈值默认落库（校方可改）
    db.add(
        AppSetting(
            key=DEMO_DATASET_MARKER_KEY,
            value={"dataset_id": DEMO_DATASET_ID, "mock_only": True},
            description="仓库内置模拟数据来源标记；清空/重建前必须核验",
            created_by="seed",
        )
    )
    db.add(
        AppSetting(
            key=RISK_THRESHOLDS_KEY,
            value=dict(DEFAULT_RISK_THRESHOLDS),
            description="预警规则引擎阈值（services/risk.py），校方可改",
            created_by="seed",
        )
    )
    db.add(
        AppSetting(
            key=DASHBOARD_LIGHT_THRESHOLDS_KEY,
            value=dict(DEFAULT_DASHBOARD_LIGHT_THRESHOLDS),
            description="院系红绿灯落实率阈值（M1 驾驶舱），校方可改",
            created_by="seed",
        )
    )
    db.add(
        AppSetting(
            key=EMPLOYMENT_TARGET_KEY,
            value={
                "target_rate": DEFAULT_EMPLOYMENT_TARGET_RATE,
                "revision": 1,
                "note": "演示数据默认目标，仅用于虚构场景",
                "updated_by": "seed",
            },
            description="学校就业去向落实率目标；由就业中心配置并留痕",
            created_by="seed",
        )
    )
    db.add(
        AppSetting(
            key=JOB_MATCH_WEIGHTS_KEY,
            value={
                "major": DEFAULT_MATCH_WEIGHTS.major,
                "direction": DEFAULT_MATCH_WEIGHTS.direction,
                "hard": DEFAULT_MATCH_WEIGHTS.hard,
            },
            description="岗位匹配打分权重（T4 M4），校方可改",
            created_by="seed",
        )
    )
    db.add(
        AppSetting(
            key=PROFILE_SCORE_WEIGHTS_KEY,
            value=DEFAULT_PROFILE_SCORE_WEIGHTS,
            description="学生画像六维确定性打分权重（T5 M3），校方可改",
            created_by="seed",
        )
    )
    db.flush()

    # 2) 学院 / 专业 / 研究方向
    colleges: list[College] = []
    majors_by_college: dict[int, list[Major]] = {}
    for cname, spec in COLLEGES.items():
        col = College(name=cname, created_by="seed")
        db.add(col)
        db.flush()
        colleges.append(col)
        majors_by_college[col.id] = []
        for code, mname in spec["majors"]:
            m = Major(code=code, name=mname, level=DegreeLevel.BACHELOR.value,
                      college_id=col.id, created_by="seed")
            db.add(m)
            db.flush()
            majors_by_college[col.id].append(m)
        for dname, kws in spec["directions"]:
            db.add(ResearchDirection(name=dname, keywords=kws, college_id=col.id, created_by="seed"))
    db.flush()
    counts["colleges"] = len(colleges)
    counts["majors"] = sum(len(v) for v in majors_by_college.values())

    # 3) 活动 / 招聘会
    activities: list[Activity] = []
    for i in range(N_ACTIVITIES):
        a = Activity(
            name=f"就业指导活动第{i + 1}期",
            category=rng.choice(["宣讲会", "双选会", "职业规划", "简历门诊", "训练营"]),
            held_at=AS_OF - timedelta(days=rng.randint(10, 200)),
            created_by="seed",
        )
        db.add(a)
        activities.append(a)
    db.flush()
    counts["activities"] = len(activities)

    fairs: list[Fair] = []
    fair_types = ["综合招聘会", "行业专场", "综合招聘会", "行业专场"]
    for i in range(N_FAIRS):
        random_days = rng.randint(5, 120)
        held_at = AS_OF if i == 0 else AS_OF - timedelta(days=random_days)
        status = "签到进行中" if i == 0 else "已归档"
        f = Fair(name=f"{GRAD_YEAR}届校园招聘会第{i + 1}场",
                 fair_type=fair_types[i],
                 held_at=held_at,
                 location=rng.choice(["体育馆", "大学生活动中心", "综合楼报告厅"]),
                 status=status,
                 registration_opens_at=held_at - timedelta(days=21),
                 registration_closes_at=held_at - timedelta(hours=2),
                 checkin_started_at=held_at - timedelta(hours=1),
                 checkin_ended_at=None if i == 0 else held_at + timedelta(hours=5),
                 archived_at=None if i == 0 else held_at + timedelta(days=1),
                 created_by="seed")
        db.add(f)
        fairs.append(f)
    db.flush()
    counts["fairs"] = len(fairs)

    # 4) 学生 + 求职行为 + 测评 + 签到；同步计算 risk 聚合
    students: list[Student] = []
    # 先按学院累计投递量用于院系均值
    per_college_apply: dict[int, list[int]] = {c.id: [] for c in colleges}
    student_aggs: list[dict] = []

    seq = 0
    job_action_rows: list[JobAction] = []
    assessment_rows: list[Assessment] = []
    attendance_rows: list[ActivityAttendance] = []

    for _ in range(N_STUDENTS):
        seq += 1
        col = rng.choice(colleges)
        major = rng.choice(majors_by_college[col.id])
        status = _weighted_status(rng)
        gender = rng.choice([Gender.MALE.value, Gender.FEMALE.value])
        student_no = f"{GRAD_YEAR}{col.id:02d}{seq:05d}"

        key_group = None
        if rng.random() < 0.08:
            key_group = rng.choice([g.value for g in KeyGroup])

        st = Student(
            student_no=student_no,
            name=names.make_name(rng),
            gender=gender,
            college_id=col.id,
            major_id=major.id,
            degree_level=DegreeLevel.BACHELOR.value,
            cohort_year=GRAD_YEAR,
            origin_place=names.make_origin(rng),
            contact_encrypted=encrypt_field(names.make_phone(rng)),
            status=status,
            is_key_group=key_group,
            created_by="seed",
        )
        db.add(st)
        students.append(st)

        # 生成求职行为 → 决定 apply_count 与最近行为距今周数
        apply_count, weeks_since = _gen_job_actions(rng, status, st, job_action_rows)
        per_college_apply[col.id].append(apply_count)
        student_aggs.append({"status": status, "apply_count": apply_count, "weeks_since": weeks_since})

        # 测评（约 60% 有）
        if rng.random() < 0.6:
            atype = rng.choice([AssessmentType.HOLLAND.value, AssessmentType.MBTI.value,
                                AssessmentType.SELF_REPORT.value])
            assessment_rows.append(Assessment(
                student=st, type=atype,
                result_json=_gen_assessment_result(rng, atype),
                term=f"{GRAD_YEAR - 1}-{GRAD_YEAR}-1", created_by="seed"))

        # 活动签到（0-4 场）——用 activity_id 避免触发 Activity.attendance 反向集
        for act in rng.sample(activities, k=rng.randint(0, 4)):
            attendance_rows.append(ActivityAttendance(
                student=st, activity_id=act.id,
                source=rng.choice([s.value for s in AttendanceSource]),
                checked_at=act.held_at or AS_OF, created_by="seed"))

    db.flush()  # 拿到 student.id
    db.add_all(job_action_rows)
    db.add_all(assessment_rows)
    db.add_all(attendance_rows)
    db.flush()
    counts["students"] = len(students)
    counts["job_actions"] = len(job_action_rows)
    counts["assessments"] = len(assessment_rows)
    counts["activity_attendance"] = len(attendance_rows)

    # 5) risk_level 回填（读 app_settings 阈值 → 纯函数评估）
    thresholds = load_risk_thresholds(db)
    college_avg = {cid: (sum(v) / len(v) if v else 0.0) for cid, v in per_college_apply.items()}
    risk_dist = {"green": 0, "yellow": 0, "red": 0}
    for st, agg in zip(students, student_aggs, strict=True):
        result = evaluate_risk(
            RiskInput(
                status=agg["status"],
                is_graduation_year=(st.cohort_year == GRAD_YEAR),
                current_month=AS_OF.month,
                weeks_since_last_job_action=agg["weeks_since"],
                apply_count=agg["apply_count"],
                college_avg_apply=college_avg[st.college_id],
            ),
            thresholds,
        )
        st.risk_level = result.level.value
        st.risk_reason = "；".join(result.reasons) if result.reasons else None
        risk_dist[result.level.value] += 1
    db.flush()
    counts["risk_red"] = risk_dist["red"]
    counts["risk_yellow"] = risk_dist["yellow"]
    counts["risk_green"] = risk_dist["green"]

    # 6) 招聘会企业 / 签到（实名+匿名）/ 场景聚合
    checkin_rows: list[FairCheckin] = []
    scene_rows: list[SceneMetric] = []
    fair_company_rows: list[FairCompany] = []
    for f in fairs:
        for j in range(rng.randint(15, 30)):
            title = names.make_job_title(rng)
            status = "已拒绝" if j % 10 == 9 else ("已报名" if j % 5 == 4 else "已通过")
            fair_company_rows.append(FairCompany(
                fair_id=f.id, company=names.make_company(rng),
                title=title,
                raw_text=f"模拟数据：招聘{title}，具体要求以演示岗位说明为准。",
                booth=f"{chr(65 + j % 6)}{j + 1:02d}",
                status=status,
                created_by="seed",
            ))
        # 实名签到（抽样学生）+ 匿名计数行
        for st in rng.sample(students, k=rng.randint(80, 150)):
            checkin_rows.append(FairCheckin(
                fair_id=f.id, student_id=st.id, gate_id=rng.choice(["G1", "G2", "G3"]),
                at=(f.held_at or AS_OF), created_by="seed"))
        checkin_rows.append(FairCheckin(
            fair_id=f.id, anonymous_count=rng.randint(200, 500),
            gate_id="G0", at=(f.held_at or AS_OF), created_by="seed"))
        for zone in ["A区", "B区", "宣讲厅"]:
            for h in range(4):
                scene_rows.append(SceneMetric(
                    fair_id=f.id, zone=zone,
                    ts_bucket=(f.held_at or AS_OF) + timedelta(hours=h),
                    headcount=rng.randint(30, 200),
                    dwell_avg_s=float(rng.randint(60, 900)), created_by="seed"))
    db.add_all(fair_company_rows)
    db.add_all(checkin_rows)
    db.add_all(scene_rows)
    db.flush()
    counts["fair_companies"] = len(fair_company_rows)
    counts["fair_checkins"] = len(checkin_rows)
    counts["scene_metrics"] = len(scene_rows)

    # 7) 岗位（300 条原文，解析留待 T2/T4）。裁决#2：≥85% 带职责描述使专业可推断，
    #    其余精简岗位专业无从推断，交由 T2 预计算的人工复核队列接住。
    jobs = []
    job_seed_specs: list[tuple[Job, bool]] = []
    for i in range(N_JOBS):
        with_duties = (i % 100) >= 15  # 每 100 条中 85 条带职责
        title, company, raw = names.make_job_posting(rng, with_duties=with_duties)
        job = Job(
            company=company, title=title, raw_text=raw,
            source=rng.choice([s.value for s in JobSource]),
            received_at=AS_OF - timedelta(days=rng.randint(1, 180)), created_by="seed")
        jobs.append(job)
        job_seed_specs.append((job, with_duties))
    db.add_all(jobs)
    db.flush()
    counts["jobs"] = len(jobs)

    # 让模拟岗位成为可直接浏览的产品数据，而不是只有“300 条原文”却没有可用岗位库。
    # 说明完整的岗位预计算为可用状态；少量精简岗位进入人工确认队列。
    parsed_jobs: list[JobParse] = []
    for index, (job, with_duties) in enumerate(job_seed_specs):
        # 保留少量未解析岗位，供正式环境的解析、失败与人工确认流程回归使用。
        if index >= N_JOBS - 5:
            continue
        location = next(
            (city for city in ["北京", "上海", "深圳", "杭州", "广州", "成都", "西安", "武汉", "南京", "苏州"] if f"工作地点：{city}" in job.raw_text or f"工作地点{city}" in job.raw_text),
            "不限",
        )
        parsed_jobs.append(JobParse(
            job_id=job.id,
            degree_req="本科",
            major_codes=JOB_MAJOR_CODES.get(job.title, []),
            direction_keywords=[job.title.replace("工程师", "").replace("研究员", "")],
            location=location,
            headcount=3,
            parse_confidence=0.92 if with_duties else 0.58,
            llm_meta={"provider": "precomputed", "prompt_ver": "demo-job-library-v1"},
            needs_review=not with_duties,
            review_reason=None if with_duties else "职位说明较简略，需要确认适合专业",
            created_by="seed",
        ))
    db.add_all(parsed_jobs)
    db.flush()
    counts["job_parses"] = len(parsed_jobs)

    # 8) 访企拓岗（M7）
    visits = []
    for _ in range(15):
        visits.append(CompanyVisit(
            company=names.make_company(rng),
            visitors=names.make_name(rng),
            visited_on=(AS_OF - timedelta(days=rng.randint(10, 300))).date(),
            commitments_json={"promised_headcount": rng.randint(2, 20)},
            follow_status=rng.choice(["跟进中", "已提交JD", "已录用"]), created_by="seed"))
    db.add_all(visits)
    db.flush()
    counts["company_visits"] = len(visits)

    # 9) 四角色演示账号元数据（口令由独立凭据发布流程生成并激活）。
    created_users = seed_demo_users(
        db,
        commit=False,
        counselor_college_id=colleges[0].id,
    )
    counts["demo_users"] = created_users

    from app.services.assist import ensure_assist_cases_for_red_students

    assist_cases = ensure_assist_cases_for_red_students(
        db,
        actor="seed",
        opened_at=DEMO_AS_OF_DATE,
    )
    counts["assist_cases"] = len(assist_cases)

    counts.update(_seed_s6_story_data(db, students, jobs))

    db.flush()
    from app.services.dashboard import refresh_dashboard_views

    refresh_dashboard_views(db, concurrently=False, commit=False)
    if commit:
        db.commit()
    return counts


def _seed_s6_story_data(
    db: Session,
    students: list[Student],
    jobs: list[Job],
) -> dict[str, int]:
    """补齐可跨端讲述的固定 mock 事实，不改变 2100/300 总规模。"""
    priority_students = sorted(
        (student for student in students if student.risk_level == RiskLevel.RED.value),
        key=lambda student: student.id,
    )
    job_candidate_groups: list[list[Student]] = []
    story_students: list[Student] = []
    story_student_ids: set[int] = set()
    for fixture_index, (_, title, major_codes) in enumerate(STORY_JOB_FIXTURES):
        if fixture_index == 5:
            # 两个数字类岗位复用同一组学生，形成同一学生收到多个相关岗位的真实场景。
            job_candidate_groups.append(job_candidate_groups[0])
            continue
        candidates = [
            student for student in priority_students
            if student.major.code in major_codes
        ][:5]
        if len(candidates) < 5:
            raise RuntimeError(f"S6 mock story requires five relevant students for {title}")
        job_candidate_groups.append(candidates)
        # 五个专业组本应得到 25 人；语言组保留一人为普通模拟学生，固定故事学生总数为 24。
        fixed_candidates = candidates[:4] if fixture_index == 4 else candidates
        for student in fixed_candidates:
            if student.id not in story_student_ids:
                story_students.append(student)
                story_student_ids.add(student.id)
    if len(story_students) != 24:
        raise RuntimeError("S6 mock story requires exactly 24 priority students")

    for index, student in enumerate(story_students, start=1):
        student.name = f"模拟故事学生{index:02d}"
        student.contact_encrypted = encrypt_field(f"1380000{index:04d}")
        student.origin_place = "模拟生源地"

    cases = db.execute(
        select(AssistCase).where(
            AssistCase.student_id.in_([student.id for student in story_students])
        )
    ).scalars().all()
    case_by_student = {case.student_id: case for case in cases}
    if len(case_by_student) != len(story_students):
        raise RuntimeError("S6 mock story requires one assist case per story student")

    actions = [
        AssistActionType.INTERVIEW.value,
        AssistActionType.PUSH_JOB.value,
        AssistActionType.TRAINING.value,
        AssistActionType.OTHER.value,
    ]
    records: list[AssistRecord] = []
    for index in range(40):
        student = story_students[index % len(story_students)]
        case = case_by_student[student.id]
        action_type = actions[index % len(actions)]
        note = f"模拟数据：完成第 {index + 1} 次就业跟进并约定下一步。"
        actor = "模拟就业办老师"
        records.append(
            AssistRecord(
                case_id=case.id,
                action_type=action_type,
                note=note,
                actor=actor,
                occurred_at=DEMO_AS_OF_DATE - timedelta(days=40 - index),
                idempotency_key=deterministic_assist_idempotency_key(
                    f"s6-story-seed:{index + 1}"
                ),
                request_hash=assist_record_request_hash(
                    case_id=case.id,
                    action_type=action_type,
                    note=note,
                    actor=actor,
                ),
                created_by="s6-story-seed",
            )
        )
    db.add_all(records)

    story_jobs = sorted(jobs, key=lambda job: job.id)[:6]
    for index, job in enumerate(story_jobs, start=1):
        company, title, major_codes = STORY_JOB_FIXTURES[index - 1]
        job.company = company
        job.title = title
        job.raw_text = f"模拟数据：{company}招聘{title}，本科及以上，专业相关，工作地点武汉。"
        parse = db.execute(select(JobParse).where(JobParse.job_id == job.id)).scalar_one()
        parse.degree_req = "本科"
        parse.major_codes = major_codes
        parse.direction_keywords = [title, "就业服务"]
        parse.location = "武汉"
        parse.headcount = 5
        parse.parse_confidence = 0.95
        parse.llm_meta = {"provider": "precomputed", "prompt_ver": "s6-mock-v2"}
        parse.needs_review = False
        parse.review_reason = None

    matches: list[Match] = []
    for job_index, (job, candidates) in enumerate(zip(story_jobs, job_candidate_groups, strict=True)):
        for candidate_index, student in enumerate(candidates):
            index = job_index * 5 + candidate_index
            feedback = None
            if index < 5:
                feedback = MatchFeedback.READ.value
            elif index < 10:
                feedback = MatchFeedback.APPLIED.value
            elif index < 15:
                feedback = MatchFeedback.UNSUITABLE.value
            matches.append(
                Match(
                    job_id=job.id,
                    student_id=student.id,
                    score=72 + index % 20,
                    reason_text="模拟数据：学生专业与岗位要求一致。",
                    pushed_at=DEMO_AS_OF_DATE - timedelta(days=30 - index),
                    feedback=feedback,
                    feedback_at=(
                        DEMO_AS_OF_DATE - timedelta(days=29 - index)
                        if feedback is not None
                        else None
                    ),
                    created_by="s6-story-seed",
                )
            )
    db.add_all(matches)
    db.flush()

    # 固定边界岗位：已有明确解析结果，但只有零分匹配，供校方在 UI 中验证
    # “无合适人选”不会被误报为可推送成功。
    boundary_job = sorted(jobs, key=lambda job: job.id)[6]
    boundary_job.company = "模拟边界企业01"
    boundary_job.title = "模拟零匹配岗位"
    boundary_job.raw_text = "模拟数据：要求不存在的专业方向，仅用于验证零匹配和禁止误推送。"
    boundary_parse = db.execute(select(JobParse).where(JobParse.job_id == boundary_job.id)).scalar_one()
    boundary_parse.degree_req = "博士"
    boundary_parse.major_codes = ["模拟不存在专业"]
    boundary_parse.direction_keywords = ["模拟零匹配"]
    boundary_parse.location = "武汉"
    boundary_parse.headcount = 1
    boundary_parse.parse_confidence = 0.99
    boundary_parse.llm_meta = {"provider": "precomputed", "prompt_ver": "s6-boundary-v1"}
    boundary_parse.needs_review = False
    boundary_parse.review_reason = None
    boundary_matches = [
        Match(
            job_id=boundary_job.id,
            student_id=student.id,
            score=0,
            reason_text="模拟数据：专业和学历要求均不满足，暂不推荐。",
            pushed_at=None,
            feedback=None,
            created_by="s6-boundary-seed",
        )
        for student in story_students[:5]
    ]
    db.add_all(boundary_matches)
    db.flush()

    # 24 名固定故事学生均生成确定性画像，确保就业中心和学院辅导员首次进入
    # 学生跟进页时就能完成“画像→帮扶→推岗→反馈→结果”的讲述。
    for student in story_students:
        rebuild_profile_for_student(
            db,
            student.id,
            term="2024-2025-1",
            actor="s6-story-seed",
            provider=None,
        )
    db.flush()
    return {
        "fixed_story_students": len(story_students),
        "story_profiles": len(story_students),
        "story_assist_records": len(records),
        "story_targeted_pushes": len(matches),
        "boundary_zero_matches": len(boundary_matches),
        "story_feedback_read": 5,
        "story_feedback_applied": 5,
        "story_feedback_unsuitable": 5,
    }


def _gen_job_actions(
    rng: random.Random, status: str, student: Student, sink: list[JobAction]
) -> tuple[int, int | None]:
    """按去向生成求职行为，返回 (apply_count, weeks_since_last_action)。"""
    apply_count = 0
    last_days: int | None = None

    def add(kind: str, days_ago: int) -> None:
        nonlocal last_days
        sink.append(JobAction(
            student=student, type=kind,
            target=names.make_company(rng),
            occurred_at=AS_OF - timedelta(days=days_ago),
            source="导入", created_by="seed"))
        last_days = days_ago if last_days is None else min(last_days, days_ago)

    if status == EmploymentStatus.SIGNED.value:
        apply_count = rng.randint(6, 14)
        for _ in range(apply_count):
            add(JobActionType.APPLY.value, rng.randint(20, 150))
        for _ in range(rng.randint(2, 5)):
            add(JobActionType.INTERVIEW.value, rng.randint(10, 60))
        add(JobActionType.OFFER.value, rng.randint(7, 30))
        add(JobActionType.SIGN.value, rng.randint(1, 21))
    elif status == EmploymentStatus.UNSETTLED.value:
        bucket = rng.random()
        if bucket < 0.40:
            # 停滞/从无 → 红（零行为 4 周+）
            if rng.random() < 0.5:
                apply_count = 0  # 从无求职行为
            else:
                apply_count = rng.randint(1, 4)
                for _ in range(apply_count):
                    add(JobActionType.APPLY.value, rng.randint(35, 120))
        elif bucket < 0.75:
            # 近期活跃但投递偏少 → 黄
            apply_count = rng.randint(1, 3)
            for _ in range(apply_count):
                add(JobActionType.APPLY.value, rng.randint(3, 20))
        else:
            # 近期活跃且投递充分 → 绿
            apply_count = rng.randint(6, 12)
            for _ in range(apply_count):
                add(JobActionType.APPLY.value, rng.randint(2, 25))
    else:
        # 升学/出国/入伍/灵活/暂不就业：少量或零求职行为（不纳入预警）
        apply_count = rng.randint(0, 3)
        for _ in range(apply_count):
            add(JobActionType.APPLY.value, rng.randint(20, 120))

    weeks_since = None if last_days is None else last_days // 7
    return apply_count, weeks_since


def _gen_assessment_result(rng: random.Random, atype: str) -> dict:
    if atype == AssessmentType.HOLLAND.value:
        codes = rng.sample(["R", "I", "A", "S", "E", "C"], k=3)
        return {"code": "".join(codes), "scores": {c: rng.randint(20, 100) for c in codes}}
    if atype == AssessmentType.MBTI.value:
        return {"type": "".join(rng.choice(p) for p in ["EI", "SN", "TF", "JP"])}
    return {"answers": {f"q{i}": rng.randint(1, 5) for i in range(1, 11)}}
