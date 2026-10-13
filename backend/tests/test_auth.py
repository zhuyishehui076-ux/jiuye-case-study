"""登录闭环：四种角色均能登录并取回身份（DONE 判据核心）。"""

from __future__ import annotations

import secrets

import pytest

from tests.conftest import login

FOUR_ROLES = [('admin', 'admin'), ('office', 'employment_office'), ('counselor', 'counselor'), ('leader', 'leader_readonly')]


@pytest.mark.parametrize("username,expected_role", FOUR_ROLES)
def test_four_roles_can_login(client, username, expected_role, demo_passwords):
    token = login(client, username, demo_passwords[username])
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["username"] == username
    assert body["role"] == expected_role
    # demo 档：角标常显
    assert body["show_demo_badge"] is True
    assert body["profile"] == "demo"


def test_counselor_carries_college_scope(client, demo_passwords):
    token = login(client, "counselor", demo_passwords['counselor'])
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.json()["college_id"] == 1


def test_wrong_password_rejected(client, demo_passwords):
    wrong_password = secrets.token_urlsafe(32)
    while wrong_password == demo_passwords["admin"]:
        wrong_password = secrets.token_urlsafe(32)
    resp = client.post(
        "/api/v1/auth/login",
        data={"username": "admin", "password": wrong_password},
    )
    assert resp.status_code == 401


def test_me_requires_token(client):
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401


def test_roles_endpoint_lists_four(client):
    resp = client.get("/api/v1/auth/roles")
    assert resp.status_code == 200
    assert set(resp.json()["roles"]) == {
        "admin",
        "employment_office",
        "counselor",
        "leader_readonly",
    }
