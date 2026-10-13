"""接口级 RBAC 隔离骨架：四角色在三个探针端点上的可达性矩阵。"""

from __future__ import annotations

import pytest

from tests.conftest import login


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    "username,admin_only,staff,readonly",
    [('admin', 200, 200, 200), ('office', 403, 200, 200), ('counselor', 403, 200, 200), ('leader', 403, 403, 200)],
)
def test_role_access_matrix(client, username, admin_only, staff, readonly, demo_passwords):
    token = login(client, username, demo_passwords[username])
    assert client.get("/api/v1/rbac/admin-only", headers=_auth(token)).status_code == admin_only
    assert client.get("/api/v1/rbac/staff", headers=_auth(token)).status_code == staff
    assert client.get("/api/v1/rbac/readonly", headers=_auth(token)).status_code == readonly


def test_leader_readonly_blocked_from_staff(client, demo_passwords):
    """只读领导角色不得进入任何写/办公端点（§6.1 领导只读无写入口）。"""
    token = login(client, "leader", demo_passwords['leader'])
    resp = client.get("/api/v1/rbac/staff", headers=_auth(token))
    assert resp.status_code == 403


def test_no_token_is_401_not_403(client):
    assert client.get("/api/v1/rbac/admin-only").status_code == 401
