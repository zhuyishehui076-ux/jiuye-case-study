"""SQLAlchemy 模型（SPEC §4 为唯一事实源）。

导入全部模型，确保 Base.metadata 完整注册（Alembic 基线与关系解析依赖此处）。
"""

from app.models.assist import AssistCase, AssistRecord
from app.models.audit import AuditLog
from app.models.behavior import (
    Activity,
    ActivityAttendance,
    Assessment,
    JobAction,
    Profile,
)
from app.models.demo_credential import DemoCredentialRelease
from app.models.enterprise_portal import (
    EnterpriseAccessToken,
    EnterpriseAccount,
    EnterpriseActivationMaterial,
    EnterpriseApplicationEvent,
    EnterpriseJobSubmission,
    EnterpriseOrganization,
    EnterpriseSession,
)
from app.models.fair import (
    Fair,
    FairCheckin,
    FairCompany,
    FairEnterpriseCheckin,
    FairSnapshot,
    SceneMetric,
)
from app.models.import_batch import ImportBatch, ImportRun, ImportTargetClaim
from app.models.job import Job, JobParse, Match
from app.models.llm_batch import LLMBatchItem, LLMBatchTask
from app.models.operation_receipt import OperationReceipt
from app.models.org import AcademicClass, College, Major, ResearchDirection
from app.models.setting import AppSetting
from app.models.student import Student
from app.models.student_portal import (
    JobApplication,
    StudentAccount,
    StudentActivationMaterial,
    StudentBinding,
    StudentConsent,
    StudentSession,
)
from app.models.user import AuthThrottle, ManagementSession, Role, User
from app.models.visit import CompanyVisit

__all__ = [
    "Role",
    "User",
    "ManagementSession",
    "AuthThrottle",
    "College",
    "Major",
    "AcademicClass",
    "ResearchDirection",
    "Student",
    "StudentBinding",
    "StudentConsent",
    "StudentAccount",
    "StudentActivationMaterial",
    "StudentSession",
    "JobApplication",
    "EnterpriseAccessToken",
    "EnterpriseOrganization",
    "EnterpriseAccount",
    "EnterpriseActivationMaterial",
    "EnterpriseSession",
    "EnterpriseJobSubmission",
    "EnterpriseApplicationEvent",
    "DemoCredentialRelease",
    "Assessment",
    "Activity",
    "ActivityAttendance",
    "JobAction",
    "Profile",
    "AssistCase",
    "AssistRecord",
    "Job",
    "JobParse",
    "Match",
    "LLMBatchTask",
    "LLMBatchItem",
    "Fair",
    "FairCompany",
    "FairCheckin",
    "FairEnterpriseCheckin",
    "FairSnapshot",
    "SceneMetric",
    "ImportBatch",
    "ImportRun",
    "ImportTargetClaim",
    "CompanyVisit",
    "AuditLog",
    "AppSetting",
    "OperationReceipt",
]
