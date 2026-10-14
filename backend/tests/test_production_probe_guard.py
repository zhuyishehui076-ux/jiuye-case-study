"""Reject unsafe production probe settings before importing the application."""

from __future__ import annotations

import secrets
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest

from tests.production_job_probe import ProbeFailure, validate_target


def probe_environment(monkeypatch):
    nonce = uuid4().hex
    database = f"jiuye_case_prod_test_{nonce}"
    role = f"case_prod_{nonce}"
    values = {
        "JIUYE_PROD_PROBE_DATABASE": database,
        "JIUYE_PROD_PROBE_ROLE": role,
        "INSTALLATION_ID": f"jiuye-case-production-test-{nonce}",
        "APP_PROFILE": "production", "DEPLOYMENT_MODE": "local",
        "JWT_SECRET": secrets.token_urlsafe(32),
        "FIELD_ENCRYPTION_KEY": secrets.token_urlsafe(32),
        "UPLOAD_DIR": "/tmp/jiuye-prod-import-probe-guard/uploads",
        "DATABASE_URL": urlunsplit(("postgresql+psycopg", f"{role}:{secrets.token_urlsafe(32)}@postgres:5432",
                                     f"/{database}", "", "")),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values, database, nonce


def test_production_probe_accepts_explicit_runtime_target(monkeypatch):
    _values, database, nonce = probe_environment(monkeypatch)
    assert validate_target("verify") == (database, nonce)


@pytest.mark.parametrize("violation", [
    "database", "host", "actor", "profile", "installation", "uploads", "shared_secret", "password",
])
def test_production_probe_refuses_unsafe_target_before_app_import(monkeypatch, violation):
    values, _database, _nonce = probe_environment(monkeypatch)
    url = urlsplit(values["DATABASE_URL"])
    overrides = {
        "database": ("DATABASE_URL", urlunsplit(url._replace(path="/unowned_production"))),
        "host": ("DATABASE_URL", values["DATABASE_URL"].replace("@postgres:", "@external-host:")),
        "actor": ("DATABASE_URL", urlunsplit(url._replace(netloc=f"other_role:{url.password}@postgres:5432"))),
        "profile": ("APP_PROFILE", "demo"),
        "installation": ("INSTALLATION_ID", "jiuye-case-test-another-instance"),
        "uploads": ("UPLOAD_DIR", "/data/uploads"),
        "shared_secret": ("FIELD_ENCRYPTION_KEY", values["JWT_SECRET"]),
        "password": ("DATABASE_URL", urlunsplit(url._replace(netloc=f"{url.username}@postgres:5432"))),
    }
    key, value = overrides[violation]
    monkeypatch.setenv(key, value)
    with pytest.raises(ProbeFailure):
        validate_target("verify")
