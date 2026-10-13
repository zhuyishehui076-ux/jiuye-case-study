"""集中收敛配置 —— 数据档位与部署拓扑正交（SPEC v1.7 §3/§14）。

纪律：业务代码不得直接读 APP_PROFILE / DEPLOYMENT_MODE 环境变量，只依赖
本模块 settings 暴露的能力开关。

零出网守卫（第①层，配置层）：deployment=local 时若配置外部 LLM 密钥，
Settings 构造直接抛异常。production 数据档同样默认禁止外部 LLM。
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Profile(StrEnum):
    DEMO = "demo"
    LOCAL = "local"
    PRODUCTION = "production"


class DeploymentMode(StrEnum):
    CLOUD = "cloud"
    LOCAL = "local"


class LocalLlmMode(StrEnum):
    GPU_REALTIME = "gpu_realtime"
    CPU_BATCH = "cpu_batch"


class ConfigError(RuntimeError):
    """配置层守卫触发的启动期错误（明确报错，拒绝启动）。"""


_INSTALLATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{15,127}$")
_BUILD_COMMIT_PATTERN = re.compile(r"^(?:unknown|[0-9a-f]{40})$")
_KNOWN_JWT_PLACEHOLDERS = {
    "dev-insecure-change-me",
    "change-me-on-install",
    "change-me-in-real-demo",
}
_KNOWN_FIELD_KEY_PLACEHOLDERS = {
    "change-me-on-install-independent-field-key",
    "change-me-in-real-demo-independent-field-key",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,
        hide_input_in_errors=True,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- 档位（唯一开关）----
    app_profile: Profile = Field(alias="APP_PROFILE")
    deployment_mode: DeploymentMode = Field(alias="DEPLOYMENT_MODE")

    # ---- 应用 ----
    app_name: str = "就业数智系统"
    api_v1_prefix: str = "/api/v1"
    installation_id: str = Field(default="development", alias="INSTALLATION_ID")
    build_commit: str = Field(default="unknown", alias="BUILD_COMMIT")

    # ---- 数据库 ----
    database_url: str = Field(alias="DATABASE_URL")
    database_pool_size: int = Field(
        default=10, ge=1, le=50, alias="DATABASE_POOL_SIZE"
    )
    database_max_overflow: int = Field(
        default=10, ge=0, le=50, alias="DATABASE_MAX_OVERFLOW"
    )
    database_pool_timeout_seconds: int = Field(
        default=10, ge=1, le=120, alias="DATABASE_POOL_TIMEOUT_SECONDS"
    )
    database_pool_recycle_seconds: int = Field(
        default=1800, ge=60, le=86400, alias="DATABASE_POOL_RECYCLE_SECONDS"
    )

    # ---- 鉴权 / JWT ----
    jwt_secret: str = Field(alias="JWT_SECRET")
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = Field(default="jiuye-api", alias="JWT_ISSUER")
    jwt_audience: str = Field(default="jiuye-first-party", alias="JWT_AUDIENCE")
    jwt_expire_minutes: int = Field(
        default=480, ge=5, le=1440, alias="JWT_EXPIRE_MINUTES"
    )
    management_session_minutes: int = Field(
        default=120, ge=5, le=480, alias="MANAGEMENT_SESSION_MINUTES"
    )
    auth_failure_limit: int = Field(
        default=5, ge=3, le=20, alias="AUTH_FAILURE_LIMIT"
    )
    auth_failure_window_minutes: int = Field(
        default=15, ge=1, le=120, alias="AUTH_FAILURE_WINDOW_MINUTES"
    )
    auth_lock_minutes: int = Field(
        default=15, ge=1, le=1440, alias="AUTH_LOCK_MINUTES"
    )
    allowed_hosts_csv: str = Field(
        default="localhost,127.0.0.1,nginx,testserver", alias="ALLOWED_HOSTS"
    )

    # ---- D2 企业演示门户 ----
    enterprise_submit_limit_per_hour: int = Field(
        default=5,
        ge=1,
        le=100,
        alias="ENTERPRISE_SUBMIT_LIMIT_PER_HOUR",
    )

    # ---- 字段级加密（students.contact 等敏感字段，SPEC §4 加密存储）----
    # 未显式配置时由 jwt_secret 派生（开发/演示可用）；生产 local 档应显式下发独立密钥。
    field_encryption_key: str = Field(default="", alias="FIELD_ENCRYPTION_KEY")

    # ---- 用户上传持久化命名空间（Demo/Production 由 Compose 映射到不同卷）----
    upload_dir: str = Field(default="/data/uploads", alias="UPLOAD_DIR")

    # ---- LLM（外部 API provider 配置；local 档必须为空）----
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_base_url: str = Field(default="", alias="LLM_BASE_URL")
    llm_model: str = Field(default="", alias="LLM_MODEL")

    # ---- LLM（本地 Ollama）----
    ollama_base_url: str = Field(
        default="http://ollama:11434", alias="OLLAMA_BASE_URL"
    )
    ollama_model: str = Field(default="qwen2.5:7b-instruct", alias="OLLAMA_MODEL")
    local_llm_mode: LocalLlmMode = Field(
        default=LocalLlmMode.GPU_REALTIME,
        alias="LOCAL_LLM_MODE",
    )

    # ---------- 能力开关（业务代码只读这些，不读 profile 字符串） ----------
    @property
    def profile(self) -> Profile:
        return self.app_profile

    @property
    def deployment(self) -> DeploymentMode:
        return self.deployment_mode

    @property
    def allow_external_llm(self) -> bool:
        """仅 demo/cloud 允许外部 API；production 与 local 都 fail-closed。"""
        return (
            self.app_profile is Profile.DEMO
            and self.deployment_mode is DeploymentMode.CLOUD
        )

    @property
    def show_demo_badge(self) -> bool:
        """前端是否常显『演示环境·模拟数据』角标。"""
        return self.app_profile is Profile.DEMO

    @property
    def d2_demo_routes_enabled(self) -> bool:
        """T19 前企业演示身份路由只在 demo 暴露。"""
        return self.app_profile is Profile.DEMO

    @property
    def student_demo_auth_enabled(self) -> bool:
        """学生端是否接受受保护的 DEMO 绑定码。"""
        return self.app_profile is Profile.DEMO

    @property
    def student_production_auth_enabled(self) -> bool:
        """学生端是否启用正式账户、一次性激活和可撤销会话。"""
        return self.app_profile is Profile.PRODUCTION

    @property
    def enterprise_production_auth_enabled(self) -> bool:
        """Enterprise H5 uses formal accounts, one-time activation and sessions."""
        return self.app_profile is Profile.PRODUCTION

    @property
    def allow_demo_seed(self) -> bool:
        """Mock seed 与演示凭据只能写入 demo 数据档。"""
        return self.app_profile is Profile.DEMO

    @property
    def allow_demo_credentials(self) -> bool:
        return self.app_profile is Profile.DEMO

    @property
    def allow_user_file_uploads(self) -> bool:
        """是否允许用户文件进入后端；P-DEMO 只能使用仓库内置模拟样例。"""
        return self.app_profile is Profile.PRODUCTION

    @property
    def network_isolated(self) -> bool:
        """local 部署应用容器接 internal 网络，仅 nginx 对内网暴露。"""
        return (
            self.app_profile is Profile.LOCAL
            or self.deployment_mode is DeploymentMode.LOCAL
        )

    @property
    def llm_batch_enabled(self) -> bool:
        """无 GPU local 档将 LLM 工作转入 PostgreSQL 夜间队列。"""
        return (
            self.deployment_mode is DeploymentMode.LOCAL
            and self.local_llm_mode is LocalLlmMode.CPU_BATCH
        )

    @property
    def ollama_force_cpu(self) -> bool:
        """Ollama 请求是否显式传 num_gpu=0，防止验收机意外走 GPU。"""
        return self.llm_batch_enabled

    @property
    def has_external_llm_config(self) -> bool:
        return bool(self.llm_api_key.strip())

    @property
    def allowed_hosts(self) -> list[str]:
        """显式可信 Host；部署时通过逗号分隔值加入域名或 EIP。"""
        return [host.strip() for host in self.allowed_hosts_csv.split(",") if host.strip()]

    # ---------- 零出网守卫（配置层，启动即校验） ----------
    @model_validator(mode="after")
    def _guard_profiles(self) -> Settings:
        if self.jwt_algorithm != "HS256":
            raise ConfigError("JWT_ALGORITHM 当前仅允许固定 HS256，禁止运行期切换算法。")
        if not self.jwt_issuer.strip() or not self.jwt_audience.strip():
            raise ConfigError("JWT_ISSUER 与 JWT_AUDIENCE 必须为非空的一方系统标识。")
        if not self.allowed_hosts or "*" in self.allowed_hosts:
            raise ConfigError("ALLOWED_HOSTS 必须显式列出可信主机，禁止为空或使用通配符。")
        if not _BUILD_COMMIT_PATTERN.fullmatch(self.build_commit):
            raise ConfigError(
                "运行身份守卫触发：BUILD_COMMIT 只能是完整 40 位小写 Git SHA "
                "或 unknown。"
            )
        if (
            self.app_profile is Profile.LOCAL
            or self.deployment_mode is DeploymentMode.LOCAL
        ) and self.llm_api_key.strip():
            raise ConfigError(
                "零出网守卫触发：DEPLOYMENT_MODE=local 下检测到外部 LLM 密钥 "
                "(LLM_API_KEY)。校内私有化交付禁止数据出校，"
                "外部 API provider 不可用。请清空 LLM_API_KEY 后重启，"
                "或将部署拓扑切换为 cloud。"
            )
        if self.app_profile is Profile.PRODUCTION and self.llm_api_key.strip():
            raise ConfigError(
                "生产数据守卫触发：APP_PROFILE=production 默认禁止外部 LLM。"
                "当前任务未授权真实数据出校，请清空 LLM_API_KEY。"
            )
        if (
            self.deployment_mode is DeploymentMode.CLOUD
            and self.local_llm_mode is LocalLlmMode.CPU_BATCH
        ):
            raise ConfigError(
                "LOCAL_LLM_MODE=cpu_batch 仅适用于 DEPLOYMENT_MODE=local；"
                "cloud 拓扑不得启用本地夜间批处理。"
            )
        if self.app_profile is Profile.PRODUCTION:
            installation_id = self.installation_id.strip()
            if (
                installation_id == "development"
                or not _INSTALLATION_ID_PATTERN.fullmatch(installation_id)
            ):
                raise ConfigError(
                    "生产配置守卫触发：INSTALLATION_ID 必须是显式生成的 "
                    "16-128 字符安全随机安装身份，不得使用 development。"
                )
            if not self.database_url.startswith("postgresql"):
                raise ConfigError(
                    "生产配置守卫触发：APP_PROFILE=production 必须使用 PostgreSQL，"
                    "禁止 SQLite 或缺省内存数据库。"
                )
            jwt = self.jwt_secret.strip()
            if len(jwt) < 32 or jwt in _KNOWN_JWT_PLACEHOLDERS:
                raise ConfigError(
                    "生产配置守卫触发：JWT_SECRET 必须是至少 32 字符的独立随机密钥。"
                )
            field_key = self.field_encryption_key.strip()
            if (
                len(field_key) < 32
                or field_key == jwt
                or field_key in _KNOWN_FIELD_KEY_PLACEHOLDERS
            ):
                raise ConfigError(
                    "生产配置守卫触发：FIELD_ENCRYPTION_KEY 必须是至少 32 字符且"
                    "不同于 JWT_SECRET 的独立密钥。"
                )
        return self


_settings: Settings | None = None


def get_settings() -> Settings:
    """惰性单例，便于测试通过环境变量覆盖后重建。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reload_settings() -> Settings:
    """测试用：强制按当前环境变量重建 settings。"""
    global _settings
    refreshed = Settings()
    # ``settings`` is imported directly by guards and services throughout the
    # application. Keep that singleton object's identity stable so a reload
    # cannot make ``get_settings()`` and an imported ``settings`` reference
    # disagree about the active profile.
    for field_name in Settings.model_fields:
        setattr(settings, field_name, getattr(refreshed, field_name))
    _settings = settings
    return settings


settings = get_settings()


def require_demo_profile(action: str) -> None:
    """服务层硬守卫：production 直接调用 Mock/演示写入函数也必须失败。"""
    if not settings.allow_demo_seed:
        raise ConfigError(
            f"生产数据守卫触发：APP_PROFILE=production 禁止执行 {action}。"
        )
