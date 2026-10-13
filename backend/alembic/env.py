"""Alembic 环境。URL 与 target_metadata 从应用配置/模型取，单一事实源。

T0：仅脚手架，versions/ 为空（无业务迁移）。业务基线在 T1。
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401  引入全部模型注册到 Base.metadata（§4 全量基线）
from alembic import context
from app.core.config import settings
from app.db.base import Base

config = context.config
# Alembic stores options in ConfigParser, where a percent sign starts
# interpolation. URL-encoded strong passwords commonly contain values such
# as %21; escape them so the engine receives the original URL.
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
