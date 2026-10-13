"""Shared fixtures; integration runs must target an isolated real PostgreSQL."""

from __future__ import annotations

import os
import re
import secrets
from urllib.parse import unquote, urlsplit

import pytest


def _validate_pg_test_target(
    *,
    database_url: str,
    installation_id: str,
    profile: str,
    deployment_mode: str,
) -> None:
    """Validate only configuration, without importing the app or opening a DB."""
    expected_database = os.environ.get("JIUYE_TEST_DATABASE", "")
    expected_host = os.environ.get("JIUYE_TEST_DB_HOST", "")
    expected_port = os.environ.get("JIUYE_TEST_DB_PORT", "")
    expected_installation = os.environ.get("JIUYE_TEST_INSTALLATION_ID", "")
    if not re.fullmatch(r"jiuye_case_test_[a-z0-9_]{1,64}", expected_database):
        raise ValueError("JIUYE_TEST_DATABASE must name a dedicated case test database")
    if not expected_host or not expected_port.isdecimal() or not 1 <= int(expected_port) <= 65535:
        raise ValueError("An explicit test DB host and valid port are required")
    if (
        not expected_installation.startswith("jiuye-case-test-")
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{15,127}", expected_installation)
        or installation_id != expected_installation
    ):
        raise ValueError("The test installation identity is missing or mismatched")
    if profile != "demo" or deployment_mode != "local":
        raise ValueError("Case PG tests require APP_PROFILE=demo and DEPLOYMENT_MODE=local")
    if any(os.environ.get(key, "").strip() for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")):
        raise ValueError("External LLM configuration is forbidden in the case test runner")
    try:
        target = urlsplit(database_url)
        target_port = target.port
    except ValueError:
        raise ValueError("The test database URL is invalid") from None
    if target.scheme not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("Real PostgreSQL with the supported psycopg driver is required")
    if (
        target.hostname != expected_host
        or target_port != int(expected_port)
        or unquote(target.path) != "/" + expected_database
        or not target.username
        or not target.password
        or target.query
        or target.fragment
    ):
        raise ValueError("The DB URL must match the explicit isolated test target")


def _validate_pg_test_secrets(
    *, database_url: str, jwt_secret: str, field_encryption_key: str
) -> None:
    """Reject missing or shared test secrets before collection or DB access."""
    if len(jwt_secret.strip()) < 32:
        raise ValueError("JWT_SECRET must be explicitly generated for the test run")
    if len(field_encryption_key.strip()) < 32:
        raise ValueError("FIELD_ENCRYPTION_KEY must be explicitly generated for the test run")
    try:
        database_password = unquote(urlsplit(database_url).password or "")
    except ValueError:
        raise ValueError("The test database URL is invalid") from None
    if (
        jwt_secret == field_encryption_key
        or jwt_secret == database_password
        or field_encryption_key == database_password
    ):
        raise ValueError("Test DB, JWT and field secrets must be generated independently")


def pytest_addoption(parser):
    parser.addoption(
        "--require-pg",
        action="store_true",
        default=False,
        help="Require real isolated PG tests; reject missing targets and relevant skips",
    )


def pytest_configure(config):
    config._jiuye_pg_nodeids = set()
    config._jiuye_pg_passed = set()
    config._jiuye_pg_skipped = set()
    if config.getoption("--require-pg"):
        try:
            _validate_pg_test_target(
                database_url=os.environ.get("DATABASE_URL", ""),
                installation_id=os.environ.get("INSTALLATION_ID", ""),
                profile=os.environ.get("APP_PROFILE", ""),
                deployment_mode=os.environ.get("DEPLOYMENT_MODE", ""),
            )
            _validate_pg_test_secrets(
                database_url=os.environ.get("DATABASE_URL", ""),
                jwt_secret=os.environ.get("JWT_SECRET", ""),
                field_encryption_key=os.environ.get("FIELD_ENCRYPTION_KEY", ""),
            )
        except ValueError as exc:
            raise pytest.UsageError(str(exc)) from None


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    if not config.getoption("--require-pg"):
        return
    config._jiuye_pg_nodeids = {
        item.nodeid for item in items if "pg_engine" in item.fixturenames
    }
    if not config._jiuye_pg_nodeids:
        raise pytest.UsageError("--require-pg did not collect any PG-dependent test")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    config = item.config
    if not config.getoption("--require-pg") or item.nodeid not in config._jiuye_pg_nodeids:
        return
    if report.skipped or hasattr(report, "wasxfail"):
        config._jiuye_pg_skipped.add(item.nodeid)
    if report.when == "call" and report.passed and not hasattr(report, "wasxfail"):
        config._jiuye_pg_passed.add(item.nodeid)


def pytest_sessionfinish(session, exitstatus):
    config = session.config
    if not config.getoption("--require-pg"):
        return
    missing_passes = config._jiuye_pg_nodeids - config._jiuye_pg_passed
    if exitstatus == pytest.ExitCode.OK and (missing_passes or config._jiuye_pg_skipped):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if config.getoption("--require-pg"):
        terminalreporter.write_sep(
            "=",
            "required PG: "
            f"selected={len(config._jiuye_pg_nodeids)} "
            f"passed={len(config._jiuye_pg_passed)} "
            f"skipped_or_xfail={len(config._jiuye_pg_skipped)}",
        )


def _is_pg() -> bool:
    from app.core.config import settings

    return urlsplit(settings.database_url).scheme in {"postgresql", "postgresql+psycopg"}


@pytest.fixture(scope="session")
def pg_engine(request):
    """Migrate a validated PG instance; never migrate an implicit existing target."""
    from app.core.config import settings

    if not _is_pg():
        if request.config.getoption("--require-pg"):
            pytest.fail("Real PostgreSQL is required", pytrace=False)
        pytest.skip("需要 PostgreSQL（禁止用 sqlite 冒充）")
    try:
        _validate_pg_test_target(
            database_url=settings.database_url,
            installation_id=settings.installation_id,
            profile=settings.profile.value,
            deployment_mode=settings.deployment.value,
        )
        _validate_pg_test_secrets(
            database_url=settings.database_url,
            jwt_secret=settings.jwt_secret,
            field_encryption_key=settings.field_encryption_key,
        )
    except ValueError as exc:
        pytest.fail(str(exc), pytrace=False)

    import app.models  # noqa: F401 - Register all models before migrations.
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from app.db.session import SessionLocal, engine
    from app.services.installation import (
        ensure_installation_profile,
        preflight_existing_database,
    )

    try:
        with SessionLocal() as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            if db.scalar(text("SELECT current_database()")) != os.environ["JIUYE_TEST_DATABASE"]:
                pytest.fail("Connected database identity does not match the test target", pytrace=False)
            state = preflight_existing_database(
                db,
                expected_profile=settings.profile,
                expected_installation_id=settings.installation_id,
            )
            if state not in {"fresh", "existing"}:
                pytest.fail("A legacy database cannot be adopted by the case tests", pytrace=False)
        command.upgrade(Config("alembic.ini"), "head")
        with SessionLocal() as db:
            ensure_installation_profile(
                db,
                expected_profile=settings.profile,
                expected_installation_id=settings.installation_id,
            )
            db.commit()
    except Exception as exc:
        pytest.fail(f"PG preflight/migration failed ({type(exc).__name__})", pytrace=False)
    yield engine


@pytest.fixture
def db(pg_engine):
    """Rollback each test, preserving the original savepoint isolation."""
    from sqlalchemy.orm import Session

    conn = pg_engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        if trans.is_active:
            trans.rollback()
        conn.close()


class _GeneratedPassword(str):
    """Preserve str behavior while redacting pytest argument representations."""

    def __repr__(self) -> str:
        return "<generated test credential>"


@pytest.fixture(scope="session")
def demo_passwords():
    from app.seed.users import DEMO_ACCOUNTS

    return {username: _GeneratedPassword(secrets.token_urlsafe(32)) for username in DEMO_ACCOUNTS}


@pytest.fixture(scope="session")
def client(pg_engine, demo_passwords):
    from fastapi.testclient import TestClient

    from app.db.session import SessionLocal
    from app.main import app
    from app.seed.users import seed_demo_users

    with SessionLocal() as db:
        seed_demo_users(db, passwords=demo_passwords)
    with TestClient(app) as client:
        yield client


def login(client, username: str, password: str) -> str:
    resp = client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]
