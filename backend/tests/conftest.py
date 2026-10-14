"""Shared fixtures; integration runs must target an isolated real PostgreSQL."""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

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


def _validate_case_pg_session(db) -> None:
    """Authorize test-only writes using URL, actual DB and immutable identity."""
    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    from app.core.config import settings
    from app.services.installation import verify_installation_profile

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
    bind = db.get_bind()
    engine = getattr(bind, "engine", bind)
    if bind.dialect.name != "postgresql" or engine.url != make_url(settings.database_url):
        raise ValueError("The fixture session is bound to an unexpected database target")
    if db.scalar(text("SELECT current_database()")) != os.environ["JIUYE_TEST_DATABASE"]:
        raise ValueError("The fixture is connected to an unexpected database identity")
    verify_installation_profile(
        db,
        expected_profile=settings.profile,
        expected_installation_id=settings.installation_id,
    )


def reset_case_demo_seed(db):
    """Clear synthetic test state only after independent target/identity checks."""
    from app.seed.demo import run_demo_seed

    _validate_case_pg_session(db)
    # The existing explicit test-only reset contract preserves product defaults.
    return run_demo_seed(db, reset=True, enforce_source_guard=False)


@pytest.fixture(scope="session")
def uploads_namespace(pg_engine):
    """Claim only the isolated case test container's dedicated uploads volume."""
    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.services.storage_namespace import (
        ensure_storage_namespace,
        preflight_storage_namespace,
    )

    if os.name != "posix" or settings.upload_dir != "/data/uploads":
        pytest.fail("PG client tests require the dedicated container uploads mount", pytrace=False)
    with SessionLocal() as db:
        _validate_case_pg_session(db)
    preflight_storage_namespace(
        settings.upload_dir,
        kind="uploads",
        expected_profile=settings.profile,
        expected_installation_id=settings.installation_id,
    )
    return ensure_storage_namespace(
        settings.upload_dir,
        kind="uploads",
        expected_profile=settings.profile,
        expected_installation_id=settings.installation_id,
    )


class _GeneratedPassword(str):
    """Preserve str behavior while redacting pytest argument representations."""

    def __repr__(self) -> str:
        return "<generated test credential>"


@pytest.fixture(scope="session")
def demo_passwords():
    from app.seed.users import DEMO_ACCOUNTS

    return {username: _GeneratedPassword(secrets.token_urlsafe(32)) for username in DEMO_ACCOUNTS}


@pytest.fixture(scope="session")
def client(pg_engine, uploads_namespace, demo_passwords):
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


@pytest.fixture
def production_import_probe(pg_engine):
    """Own a new production-profile DB/role/namespace on the isolated CI server.

    The main app remains demo. Separate processes load production settings from
    an explicit environment, migrate only the newly created database and run
    the real upload/auth/write boundaries as a non-superuser DB role.
    """
    from psycopg import sql
    from sqlalchemy import create_engine

    from app.core.config import settings
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _validate_case_pg_session(db)
    target = pg_engine.url
    if (target.host, target.port, target.username) != ("postgres", 5432, "case_user"):
        pytest.fail("Production probe requires the dedicated case Compose server", pytrace=False)

    nonce = uuid4().hex
    database_name = f"jiuye_case_prod_test_{nonce}"
    runtime_role = f"case_prod_{nonce}"
    installation_id = f"jiuye-case-production-test-{nonce}"
    database_password = secrets.token_urlsafe(32)
    database_created = role_created = False

    def run_phase(phase, env, workbook=b""):
        # No secrets in argv or diagnostic output; child failures expose only
        # controlled stage labels/classes, not connection URLs or token bodies.
        try:
            result = subprocess.run(
                [sys.executable, "-m", "tests.production_job_probe", phase],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                input=base64.b64encode(workbook).decode(),
                text=True,
                capture_output=True,
                timeout=55,
                check=False,
            )
        except subprocess.TimeoutExpired:
            pytest.fail(f"Production probe {phase} timed out", pytrace=False)
        if result.returncode != 0:
            # The worker emits this fixed prefix only; never relay raw errors.
            safe = next((line for line in reversed(result.stderr.splitlines())
                         if re.fullmatch(r"PRODUCTION_PROBE_FAILED:[A-Za-z0-9_.-]+", line)), "unreported")
            pytest.fail(f"Production probe {phase} failed ({safe})", pytrace=False)
        try:
            return json.loads(result.stdout)
        except (ValueError, TypeError):
            pytest.fail("Production probe returned invalid evidence", pytrace=False)

    try:
        with pg_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            raw = conn.connection.driver_connection
            if raw.execute("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=%s)",
                           (database_name,)).fetchone()[0] or raw.execute(
                               "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=%s)",
                               (runtime_role,)).fetchone()[0]:
                pytest.fail("Production probe refuses existing resources", pytrace=False)
            raw.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD {}").format(
                sql.Identifier(runtime_role), sql.Literal(database_password),
            ))
            role_created = True
            raw.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database_name)))
            database_created = True

        with tempfile.TemporaryDirectory(prefix="jiuye-prod-import-probe-") as directory:
            env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "LD_LIBRARY_PATH") if key in os.environ}
            env.update({
                "APP_PROFILE": "production", "DEPLOYMENT_MODE": "local",
                "INSTALLATION_ID": installation_id,
                "JWT_SECRET": secrets.token_urlsafe(32),
                "FIELD_ENCRYPTION_KEY": secrets.token_urlsafe(32),
                "UPLOAD_DIR": str(Path(directory) / "uploads"),
                "ALLOWED_HOSTS": "testserver,localhost,127.0.0.1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "JIUYE_PROD_PROBE_DATABASE": database_name,
                "JIUYE_PROD_PROBE_ROLE": runtime_role,
                "JIUYE_PROD_PROBE_OFFICE_PASSWORD": secrets.token_urlsafe(32),
                "JIUYE_PROD_PROBE_LEADER_PASSWORD": secrets.token_urlsafe(32),
            })
            if len({database_password, env["JWT_SECRET"], env["FIELD_ENCRYPTION_KEY"]}) != 3:
                pytest.fail("Production probe secrets must be independent", pytrace=False)
            admin_url = target.set(database=database_name)
            env["DATABASE_URL"] = admin_url.render_as_string(hide_password=False)
            initialized = run_phase("initialize", env)
            assert initialized == {"profile": "production", "initialized": True}
            admin_engine = create_engine(admin_url, hide_parameters=True)
            try:
                with admin_engine.begin() as conn:
                    raw = conn.connection.driver_connection
                    raw.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        sql.Identifier(database_name), sql.Identifier(runtime_role),
                    ))
                    for statement in (
                        "GRANT USAGE ON SCHEMA public TO {}",
                        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}",
                        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}",
                    ):
                        raw.execute(sql.SQL(statement).format(sql.Identifier(runtime_role)))
            finally:
                admin_engine.dispose()
            env["DATABASE_URL"] = target.set(
                database=database_name, username=runtime_role, password=database_password,
            ).render_as_string(hide_password=False)

            def verify(workbook):
                evidence = run_phase("verify", env, workbook)
                with SessionLocal() as db:
                    _validate_case_pg_session(db)  # Demo identity remains intact.
                assert settings.profile.value == "demo"
                return evidence

            yield verify
    finally:
        # Delete only resources successfully created by this invocation. Never
        # adopt existing names, force-disconnect sessions or drop other DBs.
        with pg_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            raw = conn.connection.driver_connection
            if database_created:
                owner = raw.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s",
                                    (database_name,)).fetchone()
                if owner != (target.username,):
                    pytest.fail("Production probe resource ownership changed", pytrace=False)
                raw.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))
            if role_created:
                raw.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(runtime_role)))
