"""审计日志骨架（SPEC §6.8 X1）。

T0 仅提供留痕入口，落库到 audit_logs 表在 X1 完整实现（后续里程碑）。
当前实现写结构化日志，保证所有写/导出操作有统一留痕入口可挂接。
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.audit import AuditLog

logger = logging.getLogger("audit")


def record(actor: str, action: str, obj: str, summary: str = "") -> None:
    """留痕一条审计事件（who / action / object / 摘要）。"""
    logger.info("audit actor=%s action=%s object=%s summary=%s", actor, action, obj, summary)


def record_db(
    db: Session,
    *,
    actor: str,
    actor_id: int | None = None,
    actor_role: str | None = None,
    source_channel: str | None = None,
    correlation_id: str | None = None,
    action: str,
    object_type: str,
    object_id: str | int | None = None,
    change_summary: dict | None = None,
    detail: str | None = None,
) -> AuditLog:
    """写入结构化审计日志。调用方控制事务边界。"""
    row = AuditLog(
        actor_id=actor_id,
        actor_name=actor,
        actor_role=actor_role,
        source_channel=source_channel,
        correlation_id=correlation_id,
        action=action,
        object_type=object_type,
        object_id=str(object_id) if object_id is not None else None,
        change_summary=change_summary or {},
        detail=detail,
        created_by=actor,
    )
    db.add(row)
    return row
