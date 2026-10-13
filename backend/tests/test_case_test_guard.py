"""Pure configuration safety tests; these never contact a database."""

from __future__ import annotations

import secrets
from urllib.parse import urlsplit, urlunsplit

import pytest

from tests.conftest import _validate_pg_test_secrets, _validate_pg_test_target


@pytest.fixture
def isolated_pg_inputs(monkeypatch):
    expected = {
        "JIUYE_TEST_DATABASE": "jiuye_case_test_contract",
        "JIUYE_TEST_DB_HOST": "postgres",
        "JIUYE_TEST_DB_PORT": "5432",
        "JIUYE_TEST_INSTALLATION_ID": "jiuye-case-test-contract",
    }
    for key, value in expected.items():
        monkeypatch.setenv(key, value)
    for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    authority = "case_user:" + secrets.token_urlsafe(32) + "@postgres:5432"
    return {
        "database_url": urlunsplit(
            ("postgresql+psycopg", authority, "/jiuye_case_test_contract", "", "")
        ),
        "installation_id": "jiuye-case-test-contract",
        "profile": "demo",
        "deployment_mode": "local",
    }


def test_guard_accepts_only_the_explicit_pg_target(isolated_pg_inputs):
    _validate_pg_test_target(**isolated_pg_inputs)


@pytest.mark.parametrize(
    "change",
    [
        "non_pg",
        "wrong_host",
        "wrong_port",
        "wrong_database",
        "connection_override",
        "wrong_identity",
        "production",
        "cloud",
    ],
)
def test_guard_refuses_unsafe_pg_targets(isolated_pg_inputs, change):
    inputs = dict(isolated_pg_inputs)
    url = urlsplit(inputs["database_url"])
    if change == "non_pg":
        inputs["database_url"] = "sqlite:///:memory:"
    elif change == "wrong_host":
        inputs["database_url"] = urlunsplit(url._replace(netloc=url.netloc.replace("@postgres:", "@other:")))
    elif change == "wrong_port":
        inputs["database_url"] = urlunsplit(url._replace(netloc=url.netloc.replace(":5432", ":15432")))
    elif change == "wrong_database":
        inputs["database_url"] = urlunsplit(url._replace(path="/unrelated_database"))
    elif change == "connection_override":
        inputs["database_url"] = urlunsplit(url._replace(query="host=other"))
    elif change == "wrong_identity":
        inputs["installation_id"] = "unrelated-installation-id"
    elif change == "production":
        inputs["profile"] = "production"
    elif change == "cloud":
        inputs["deployment_mode"] = "cloud"
    with pytest.raises(ValueError):
        _validate_pg_test_target(**inputs)


def test_guard_refuses_missing_target_declaration(isolated_pg_inputs, monkeypatch):
    monkeypatch.delenv("JIUYE_TEST_DATABASE")
    with pytest.raises(ValueError):
        _validate_pg_test_target(**isolated_pg_inputs)


def test_guard_refuses_external_model_configuration(isolated_pg_inputs, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "external-model")
    with pytest.raises(ValueError):
        _validate_pg_test_target(**isolated_pg_inputs)


def test_secret_guard_accepts_independent_generated_values(isolated_pg_inputs):
    _validate_pg_test_secrets(
        database_url=isolated_pg_inputs["database_url"],
        jwt_secret=secrets.token_urlsafe(48),
        field_encryption_key=secrets.token_urlsafe(48),
    )


@pytest.mark.parametrize(
    "change", ["missing_jwt", "missing_field", "shared_jwt_field", "jwt_equals_db", "field_equals_db"]
)
def test_secret_guard_refuses_missing_or_shared_values(isolated_pg_inputs, change):
    jwt_secret = secrets.token_urlsafe(48)
    field_key = secrets.token_urlsafe(48)
    database_password = urlsplit(isolated_pg_inputs["database_url"]).password
    if change == "missing_jwt":
        jwt_secret = ""
    elif change == "missing_field":
        field_key = ""
    elif change == "shared_jwt_field":
        field_key = jwt_secret
    elif change == "jwt_equals_db":
        jwt_secret = database_password
    elif change == "field_equals_db":
        field_key = database_password
    with pytest.raises(ValueError):
        _validate_pg_test_secrets(
            database_url=isolated_pg_inputs["database_url"],
            jwt_secret=jwt_secret,
            field_encryption_key=field_key,
        )
