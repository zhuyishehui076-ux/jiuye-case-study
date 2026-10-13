"""四角色定义 + 接口级 RBAC 依赖（SPEC §4 / §6.8 X1）。

四角色：admin / employment_office / counselor(限本学院) / leader_readonly。
本文件只做骨架：角色枚举 + require_roles 依赖工厂。数据范围过滤
（counselor 锁本学院）在服务层实现，T0 仅预留 college_id 载荷。
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    EMPLOYMENT_OFFICE = "employment_office"
    COUNSELOR = "counselor"
    LEADER_READONLY = "leader_readonly"


ALL_ROLES: tuple[Role, ...] = tuple(Role)

# 只读角色：不得出现在任何写操作端点（前端也做路由守卫，双层）。
READONLY_ROLES: frozenset[Role] = frozenset({Role.LEADER_READONLY})
