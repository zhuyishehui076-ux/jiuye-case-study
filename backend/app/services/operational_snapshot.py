"""PII-free, transactionally stable PostgreSQL backup reconciliation metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import Connection, inspect, text

from app.core.config import settings
from app.db.session import engine
from app.services.storage_namespace import (
    NAMESPACE_MARKER_NAME,
    StorageNamespaceError,
    verify_storage_namespace,
)

SNAPSHOT_VERSION = 3
SUPPORTED_SNAPSHOT_VERSIONS = frozenset({2, SNAPSHOT_VERSION})
BACKUP_BUNDLE_MANIFEST_VERSION = 2
SNAPSHOT_IDENTITY_KEYS = (
    "version",
    "profile",
    "deployment_mode",
    "build_commit",
    "installation_id_sha256_prefix",
    "alembic_revision",
)
SNAPSHOT_OPTIONAL_IDENTITY_KEYS = ("installation_id_sha256",)
SNAPSHOT_RELATION_KEYS = (
    "table_counts",
    "key_aggregate_digests",
    "row_content_digests",
    "column_content_digests",
    "sequence_state",
)
SOURCE_COMPARISON_IDENTITY_KEYS = (
    "profile",
    "installation_id_sha256_prefix",
    "alembic_revision",
)
_SNAPSHOT_ID_PATTERN = re.compile(r"^[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_KEYS = {
    "version",
    "kind",
    "profile",
    "installation_id",
    "installation_id_sha256",
    "filename",
    "size_bytes",
    "sha256",
    "snapshot_filename",
    "snapshot_size_bytes",
    "snapshot_sha256",
    "snapshot_schema_version",
    "source_build_commit",
    "source_alembic_revision",
    "namespace_sha256",
    "created_at_utc",
}
ComparisonMode = Literal["strict", "source"]
MIGRATION_MUTABLE_RELATIONS = frozenset({"alembic_version"})
MIGRATION_REMOVED_COLUMNS: frozenset[tuple[str, str]] = frozenset()


class BackupBundleError(RuntimeError):
    """A backup bundle is incomplete, ambiguous, or has been tampered with."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    temporary = path.with_name(
        f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}"
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _require_regular_file(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise BackupBundleError(f"{label} must be a regular non-symlink file")


def _verify_backup_namespace(path: Path) -> None:
    if path.name != NAMESPACE_MARKER_NAME:
        raise BackupBundleError("backup namespace marker has an unexpected filename")
    _require_regular_file(path, "namespace marker")
    try:
        verify_storage_namespace(
            path.parent,
            kind="backup",
            expected_profile=settings.profile,
            expected_installation_id=settings.installation_id,
        )
    except StorageNamespaceError as exc:
        raise BackupBundleError("backup namespace identity is invalid") from exc


def _assert_plain_sql_dump(path: Path) -> None:
    _require_regular_file(path, "backup dump")
    with path.open("rb") as stream:
        prefix = stream.read(8)
    if not prefix:
        raise BackupBundleError("backup dump is empty")
    # UTF-8 BOM is harmless to psql and is accepted for Windows portability.
    if prefix.startswith(b"\xef\xbb\xbf"):
        prefix = prefix[3:]
    if prefix.startswith(b"PGDMP"):
        raise BackupBundleError(
            "custom-format PGDMP is not plain SQL and cannot be restored with psql"
        )


def _relation_count(connection: Connection, *, schema: str, name: str) -> int:
    preparer = connection.dialect.identifier_preparer
    qualified = f"{preparer.quote_schema(schema)}.{preparer.quote(name)}"
    return int(connection.execute(text(f"SELECT count(*) FROM {qualified}")).scalar_one())


def _relation_columns(connection: Connection, *, schema: str, name: str) -> list[str]:
    return [
        str(value)
        for value in connection.execute(
            text(
                "SELECT attr.attname FROM pg_attribute AS attr "
                "JOIN pg_class AS relation ON relation.oid = attr.attrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = :schema AND relation.relname = :name "
                "AND attr.attnum > 0 AND NOT attr.attisdropped ORDER BY attr.attnum"
            ),
            {"schema": schema, "name": name},
        ).scalars()
    ]


def _primary_key_digest(
    connection: Connection, *, schema: str, table_name: str, columns: list[str]
) -> str:
    preparer = connection.dialect.identifier_preparer
    qualified = f"{preparer.quote_schema(schema)}.{preparer.quote(table_name)}"
    quoted = [preparer.quote(column) for column in columns]
    encoded = ", ".join(
        f"COALESCE({column}::text, '<NULL>')" for column in quoted
    )
    ordering = ", ".join(quoted)
    statement = text(
        "SELECT md5(COALESCE(string_agg("
        f"md5(concat_ws(E'\\x1f', {encoded})), '' ORDER BY {ordering}), '')) "
        f"FROM {qualified}"
    )
    return str(connection.execute(statement).scalar_one())


def _relation_content_digest(
    connection: Connection,
    *,
    schema: str,
    name: str,
    ordering_columns: list[str] | None = None,
) -> str:
    """Hash every row deterministically without emitting row content or PII.

    PostgreSQL's canonical jsonb text preserves column names and typed values.
    Tables are streamed in primary-key order; relations without a primary key
    (including materialized views) are ordered by the canonical row payload.
    Length framing prevents concatenation ambiguity.
    """
    preparer = connection.dialect.identifier_preparer
    qualified = f"{preparer.quote_schema(schema)}.{preparer.quote(name)}"
    alias = preparer.quote("snapshot_row")
    payload = f"to_jsonb({alias})::text"
    if ordering_columns:
        ordering = ", ".join(
            f"{alias}.{preparer.quote(column)}" for column in ordering_columns
        )
    else:
        ordering = payload
    statement = text(
        f"SELECT {payload} AS payload FROM {qualified} AS {alias} "
        f"ORDER BY {ordering}"
    ).execution_options(stream_results=True, yield_per=1000)
    digest = hashlib.sha256(b"jiuye-operational-row-content-v1\0")
    for value in connection.execute(statement).scalars():
        encoded = str(value).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, byteorder="big", signed=False))
        digest.update(encoded)
    return digest.hexdigest()


def _table_content_digests(
    connection: Connection,
    *,
    schema: str,
    name: str,
    columns: list[str],
    ordering_columns: list[str] | None,
) -> tuple[str, dict[str, str]]:
    """Hash full rows and every column in one deterministic streamed pass."""
    preparer = connection.dialect.identifier_preparer
    qualified = f"{preparer.quote_schema(schema)}.{preparer.quote(name)}"
    alias = preparer.quote("snapshot_row")
    payload_expression = f"to_jsonb({alias})::text"
    if ordering_columns:
        ordering = ", ".join(
            f"{alias}.{preparer.quote(column)}" for column in ordering_columns
        )
    else:
        ordering = payload_expression
    column_expressions = ", ".join(
        f"({payload_expression.replace('::text', '')} -> :column_{index})::text "
        f"AS value_{index}"
        for index, _column in enumerate(columns)
    )
    statement = text(
        f"SELECT {payload_expression} AS payload"
        f"{', ' if column_expressions else ''}{column_expressions} "
        f"FROM {qualified} AS {alias} ORDER BY {ordering}"
    ).execution_options(stream_results=True, yield_per=1000)
    parameters = {f"column_{index}": column for index, column in enumerate(columns)}
    row_digest = hashlib.sha256(b"jiuye-operational-row-content-v1\0")
    column_digests = {
        column: hashlib.sha256(
            b"jiuye-operational-column-content-v1\0" + column.encode("utf-8") + b"\0"
        )
        for column in columns
    }
    for row in connection.execute(statement, parameters).mappings():
        encoded_row = str(row["payload"]).encode("utf-8")
        row_digest.update(len(encoded_row).to_bytes(8, byteorder="big", signed=False))
        row_digest.update(encoded_row)
        for index, column in enumerate(columns):
            # Hash PostgreSQL's original canonical jsonb scalar text directly.
            # Never round-trip numeric through Python float: high-precision
            # values such as 1.00000000000000001 must remain distinguishable.
            encoded_value = str(row[f"value_{index}"]).encode("utf-8")
            column_digests[column].update(
                len(encoded_value).to_bytes(8, byteorder="big", signed=False)
            )
            column_digests[column].update(encoded_value)
    return row_digest.hexdigest(), {
        column: digest.hexdigest() for column, digest in column_digests.items()
    }


def _sequence_inventory(connection: Connection) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        text(
            "SELECT seq_ns.nspname, seq.relname, tbl_ns.nspname, tbl.relname, "
            "attr.attname, pgseq.increment_by, pgseq.min_value, "
            "pgseq.max_value, pgseq.cycle FROM pg_class AS seq "
            "JOIN pg_namespace AS seq_ns ON seq_ns.oid = seq.relnamespace "
            "JOIN pg_sequences AS pgseq ON pgseq.schemaname = seq_ns.nspname "
            "AND pgseq.sequencename = seq.relname "
            "LEFT JOIN pg_depend AS dep ON dep.classid = 'pg_class'::regclass "
            "AND dep.objid = seq.oid AND dep.deptype IN ('a', 'i') "
            "LEFT JOIN pg_class AS tbl ON tbl.oid = dep.refobjid "
            "LEFT JOIN pg_namespace AS tbl_ns ON tbl_ns.oid = tbl.relnamespace "
            "LEFT JOIN pg_attribute AS attr ON attr.attrelid = tbl.oid "
            "AND attr.attnum = dep.refobjsubid "
            "WHERE seq.relkind = 'S' AND seq_ns.nspname = 'public' "
            "ORDER BY seq.relname"
        )
    ).all()
    preparer = connection.dialect.identifier_preparer
    inventory: dict[str, dict[str, Any]] = {}
    for (
        seq_schema,
        seq_name,
        table_schema,
        table_name,
        column_name,
        increment_by,
        min_value,
        max_value,
        cycle,
    ) in rows:
        sequence = (
            f"{preparer.quote_schema(seq_schema)}.{preparer.quote(seq_name)}"
        )
        last_value, is_called = connection.execute(
            text(f"SELECT last_value, is_called FROM {sequence}")
        ).one()
        owned_by = None
        minimum_owned = None
        maximum_owned = None
        if table_schema and table_name and column_name:
            table = (
                f"{preparer.quote_schema(table_schema)}."
                f"{preparer.quote(table_name)}"
            )
            column = preparer.quote(column_name)
            minimum_owned, maximum_owned = connection.execute(
                text(f"SELECT min({column}), max({column}) FROM {table}")
            ).one()
            owned_by = f"{table_schema}.{table_name}.{column_name}"
        increment = int(increment_by)
        next_value = int(last_value) + (increment if is_called else 0)
        within_sequence_bounds = int(min_value) <= next_value <= int(max_value)
        if increment > 0:
            clear_of_owned_values = (
                maximum_owned is None or next_value > int(maximum_owned)
            )
        else:
            clear_of_owned_values = (
                minimum_owned is None or next_value < int(minimum_owned)
            )
        # Fail closed at either sequence boundary. A cycling sequence may wrap
        # into an existing key; reporting it as safe would make a restored
        # database pass reconciliation and then fail on its first write.
        safe = not bool(cycle) and within_sequence_bounds and clear_of_owned_values
        inventory[str(seq_name)] = {
            "last_value": int(last_value),
            "is_called": bool(is_called),
            "increment_by": increment,
            "min_value": int(min_value),
            "max_value": int(max_value),
            "cycle": bool(cycle),
            "next_value": next_value,
            "owned_by": owned_by,
            "min_owned_value": (
                int(minimum_owned) if minimum_owned is not None else None
            ),
            "max_owned_value": (
                int(maximum_owned) if maximum_owned is not None else None
            ),
            "within_sequence_bounds": within_sequence_bounds,
            "safe": safe,
        }
    return inventory


def refresh_derived_relations(connection: Connection) -> None:
    """Refresh dashboard materialized views inside the caller's write transaction."""
    if connection.dialect.name != "postgresql":
        raise RuntimeError("operational snapshot requires PostgreSQL")
    materialized_views = list(
        connection.execute(
            text(
                "SELECT matviewname FROM pg_matviews "
                "WHERE schemaname = 'public' "
                "AND matviewname LIKE 'mv_dashboard_%' ORDER BY matviewname"
            )
        ).scalars()
    )
    preparer = connection.dialect.identifier_preparer
    for name in materialized_views:
        qualified = f"{preparer.quote_schema('public')}.{preparer.quote(name)}"
        connection.execute(text(f"REFRESH MATERIALIZED VIEW {qualified}"))


def build_operational_snapshot(connection: Connection) -> dict[str, Any]:
    """Return counts and one-way relation digests; no row content or PII is emitted."""
    if connection.dialect.name != "postgresql":
        raise RuntimeError("operational snapshot requires PostgreSQL")
    inspector = inspect(connection)
    tables = sorted(inspector.get_table_names(schema="public"))
    materialized_views = list(
        connection.execute(
            text(
                "SELECT matviewname FROM pg_matviews "
                "WHERE schemaname = 'public' ORDER BY matviewname"
            )
        ).scalars()
    )
    revision = None
    if "alembic_version" in tables:
        revision = connection.execute(
            text("SELECT version_num FROM public.alembic_version")
        ).scalar_one_or_none()
    installation_digest = hashlib.sha256(settings.installation_id.encode()).hexdigest()
    key_digests: dict[str, str] = {}
    row_content_digests: dict[str, str] = {}
    column_content_digests: dict[str, dict[str, str]] = {}
    for table_name in tables:
        constrained = inspector.get_pk_constraint(
            table_name, schema="public"
        ).get("constrained_columns")
        if constrained:
            key_digests[table_name] = _primary_key_digest(
                connection,
                schema="public",
                table_name=table_name,
                columns=list(constrained),
            )
        columns = _relation_columns(
            connection, schema="public", name=table_name
        )
        row_digest, column_digests = _table_content_digests(
            connection,
            schema="public",
            name=table_name,
            columns=columns,
            ordering_columns=list(constrained) if constrained else None,
        )
        row_content_digests[table_name] = row_digest
        column_content_digests[table_name] = column_digests
    return {
        "version": SNAPSHOT_VERSION,
        "profile": settings.profile.value,
        "deployment_mode": settings.deployment.value,
        "build_commit": settings.build_commit,
        "installation_id_sha256": installation_digest,
        "installation_id_sha256_prefix": installation_digest[:16],
        "alembic_revision": revision,
        "table_counts": {
            name: _relation_count(connection, schema="public", name=name)
            for name in tables
        },
        "materialized_view_counts": {
            name: _relation_count(connection, schema="public", name=name)
            for name in materialized_views
        },
        "key_aggregate_digests": key_digests,
        "row_content_digests": row_content_digests,
        "column_content_digests": column_content_digests,
        "materialized_view_content_digests": {
            name: _relation_content_digest(
                connection,
                schema="public",
                name=name,
            )
            for name in materialized_views
        },
        "sequence_state": _sequence_inventory(connection),
    }


def compare_operational_snapshots(
    expected: dict[str, Any],
    actual: dict[str, Any],
    *,
    mode: ComparisonMode = "strict",
) -> list[str]:
    """Return deterministic differences for strict or pre-migration comparison."""
    differences: list[str] = []
    expected_version = expected.get("version")
    actual_version = actual.get("version")
    if mode == "strict" and expected_version != actual_version:
        differences.append(
            f"version: expected={expected_version!r}, actual={actual_version!r}"
        )
    elif mode == "source":
        if expected_version not in SUPPORTED_SNAPSHOT_VERSIONS:
            differences.append(f"version: unsupported source snapshot {expected_version!r}")
        if actual_version != SNAPSHOT_VERSION:
            differences.append(f"version: current snapshot must be {SNAPSHOT_VERSION}")
    identity_keys = (
        tuple(key for key in SNAPSHOT_IDENTITY_KEYS if key != "version")
        if mode == "strict"
        else SOURCE_COMPARISON_IDENTITY_KEYS
    )
    for key in identity_keys:
        if expected.get(key) != actual.get(key):
            differences.append(
                f"{key}: expected={expected.get(key)!r}, actual={actual.get(key)!r}"
            )
    for key in SNAPSHOT_OPTIONAL_IDENTITY_KEYS:
        if key in expected or key in actual:
            if expected.get(key) != actual.get(key):
                differences.append(
                    f"{key}: expected={expected.get(key)!r}, actual={actual.get(key)!r}"
                )
    for relation_key in SNAPSHOT_RELATION_KEYS:
        if mode == "source" and relation_key not in expected:
            # Legacy snapshots are decoded using the fields their version
            # actually authenticated; current-only stronger fields are checked
            # by the post-restore migration preservation snapshot.
            continue
        if relation_key not in expected and relation_key not in actual:
            continue
        expected_values = expected.get(relation_key)
        actual_values = actual.get(relation_key)
        if not isinstance(expected_values, dict) or not isinstance(actual_values, dict):
            differences.append(f"{relation_key}: invalid snapshot structure")
            continue
        for name in sorted(set(expected_values) | set(actual_values)):
            expected_value = expected_values.get(name)
            actual_value = actual_values.get(name)
            if expected_value != actual_value:
                differences.append(
                    f"{relation_key}.{name}: "
                    f"expected={expected_value!r}, actual={actual_value!r}"
                )
    differences.extend(_sequence_safety_differences(expected, label="expected"))
    differences.extend(_sequence_safety_differences(actual, label="actual"))
    return differences


def _sequence_safety_differences(
    snapshot: dict[str, Any], *, label: str
) -> list[str]:
    sequences = snapshot.get("sequence_state")
    if not isinstance(sequences, dict):
        return [f"{label}.sequence_state: invalid snapshot structure"]
    differences: list[str] = []
    for name, state in sorted(sequences.items()):
        if not isinstance(state, dict) or state.get("safe") is not True:
            differences.append(f"{label}.sequence_state.{name}: unsafe next value")
    return differences


def compare_migration_preservation(
    before: dict[str, Any],
    after: dict[str, Any],
) -> list[str]:
    """Prove that a schema migration preserved every pre-existing business row.

    A relation may change only through the checked-in allowlist above. Adding
    new tables is allowed; removing or modifying a pre-existing table's count,
    primary-key set, or any pre-existing column digest is rejected. New columns
    are allowed; removed columns require a checked-in, reviewable allowlist.
    """
    differences: list[str] = []
    for relation_key in ("table_counts", "key_aggregate_digests"):
        before_values = before.get(relation_key)
        after_values = after.get(relation_key)
        if not isinstance(before_values, dict) or not isinstance(after_values, dict):
            differences.append(f"{relation_key}: invalid migration snapshot structure")
            continue
        for name in sorted(before_values):
            if name in MIGRATION_MUTABLE_RELATIONS:
                continue
            if name not in after_values:
                differences.append(f"{relation_key}.{name}: relation missing after migration")
            elif before_values[name] != after_values[name]:
                differences.append(
                    f"{relation_key}.{name}: changed across migration; "
                    f"before={before_values[name]!r}, after={after_values[name]!r}"
                )
    before_columns = before.get("column_content_digests")
    after_columns = after.get("column_content_digests")
    if not isinstance(before_columns, dict) or not isinstance(after_columns, dict):
        differences.append(
            "column_content_digests: invalid migration snapshot structure"
        )
    else:
        for table_name in sorted(before_columns):
            if table_name in MIGRATION_MUTABLE_RELATIONS:
                continue
            before_table = before_columns[table_name]
            after_table = after_columns.get(table_name)
            if not isinstance(before_table, dict) or not isinstance(after_table, dict):
                differences.append(
                    f"column_content_digests.{table_name}: relation missing or invalid after migration"
                )
                continue
            for column_name in sorted(before_table):
                if column_name not in after_table:
                    if (table_name, column_name) not in MIGRATION_REMOVED_COLUMNS:
                        differences.append(
                            f"column_content_digests.{table_name}.{column_name}: "
                            "column removed without checked-in approval"
                        )
                    continue
                if before_table[column_name] != after_table[column_name]:
                    differences.append(
                        f"column_content_digests.{table_name}.{column_name}: "
                        "changed across migration"
                    )
    differences.extend(_sequence_safety_differences(after, label="post_migration"))
    return differences


def _assert_snapshot_safe_for_backup(
    snapshot: dict[str, Any], *, require_current: bool
) -> None:
    version = snapshot.get("version")
    if version not in SUPPORTED_SNAPSHOT_VERSIONS:
        raise BackupBundleError("source snapshot schema version is unsupported")
    if require_current and version != SNAPSHOT_VERSION:
        raise BackupBundleError("new backup must use the current snapshot schema")
    required_relations = (
        set(SNAPSHOT_RELATION_KEYS)
        if version == SNAPSHOT_VERSION
        else {
            "table_counts",
            "key_aggregate_digests",
            "sequence_state",
        }
    )
    required = set(SNAPSHOT_IDENTITY_KEYS) | required_relations | {
        "installation_id_sha256"
    }
    if not required.issubset(snapshot):
        raise BackupBundleError("source snapshot lacks required identity/relation fields")
    unsafe = _sequence_safety_differences(snapshot, label="source")
    if unsafe:
        raise BackupBundleError("; ".join(unsafe))


def _load_snapshot(path: Path) -> dict[str, Any]:
    _require_regular_file(path, "snapshot")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("snapshot root must be an object")
    return data


def create_backup_bundle_manifest(
    *,
    dump_path: Path,
    snapshot_path: Path,
    namespace_path: Path,
    output_path: Path,
    published_dump_name: str,
    published_snapshot_name: str,
) -> dict[str, Any]:
    """Create the final commit marker binding every backup sidecar by SHA-256."""
    _assert_plain_sql_dump(dump_path)
    _require_regular_file(snapshot_path, "source snapshot")
    _verify_backup_namespace(namespace_path)
    snapshot = _load_snapshot(snapshot_path)
    _assert_snapshot_safe_for_backup(snapshot, require_current=True)
    installation_digest = hashlib.sha256(settings.installation_id.encode()).hexdigest()
    if snapshot["profile"] != settings.profile.value:
        raise BackupBundleError("source snapshot profile does not match runtime")
    if snapshot["installation_id_sha256"] != installation_digest:
        raise BackupBundleError("source snapshot installation identity does not match")
    payload: dict[str, Any] = {
        "version": BACKUP_BUNDLE_MANIFEST_VERSION,
        "kind": "postgresql-backup-bundle",
        "profile": settings.profile.value,
        "installation_id": settings.installation_id,
        "installation_id_sha256": installation_digest,
        "filename": published_dump_name,
        "size_bytes": dump_path.stat().st_size,
        "sha256": _sha256(dump_path),
        "snapshot_filename": published_snapshot_name,
        "snapshot_size_bytes": snapshot_path.stat().st_size,
        "snapshot_sha256": _sha256(snapshot_path),
        "snapshot_schema_version": snapshot["version"],
        "source_build_commit": snapshot["build_commit"],
        "source_alembic_revision": snapshot["alembic_revision"],
        "namespace_sha256": _sha256(namespace_path),
        "created_at_utc": datetime.now(UTC).isoformat(),
    }
    _atomic_write_json(output_path, payload)
    return payload


def verify_backup_bundle(dump_path: Path) -> dict[str, Any]:
    """Strictly validate a fully published dump/snapshot/namespace bundle."""
    _assert_plain_sql_dump(dump_path)
    manifest_path = dump_path.with_name(f"{dump_path.name}.manifest.json")
    _require_regular_file(manifest_path, "backup manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BackupBundleError("backup manifest is invalid JSON") from exc
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS:
        raise BackupBundleError("backup manifest has ambiguous or missing fields")
    if manifest["version"] != BACKUP_BUNDLE_MANIFEST_VERSION:
        raise BackupBundleError("backup manifest version is unsupported")
    if manifest["kind"] != "postgresql-backup-bundle":
        raise BackupBundleError("backup manifest kind is invalid")
    for key in ("sha256", "snapshot_sha256", "namespace_sha256", "installation_id_sha256"):
        if not isinstance(manifest[key], str) or not _SHA256_PATTERN.fullmatch(manifest[key]):
            raise BackupBundleError(f"backup manifest {key} is invalid")
    if (
        type(manifest["size_bytes"]) is not int
        or manifest["size_bytes"] <= 0
        or type(manifest["snapshot_size_bytes"]) is not int
        or manifest["snapshot_size_bytes"] <= 0
        or type(manifest["snapshot_schema_version"]) is not int
        or not isinstance(manifest["source_build_commit"], str)
        or not isinstance(manifest["source_alembic_revision"], str | None)
        or not isinstance(manifest["created_at_utc"], str)
    ):
        raise BackupBundleError("backup manifest field types are invalid")
    try:
        created_at = datetime.fromisoformat(manifest["created_at_utc"])
    except ValueError as exc:
        raise BackupBundleError("backup manifest creation time is invalid") from exc
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise BackupBundleError("backup manifest creation time must be timezone-aware")
    if manifest["filename"] != dump_path.name:
        raise BackupBundleError("backup manifest filename does not match dump")
    if manifest["profile"] != settings.profile.value:
        raise BackupBundleError("backup profile does not match runtime")
    if manifest["installation_id"] != settings.installation_id:
        raise BackupBundleError("backup installation identity does not match runtime")
    installation_digest = hashlib.sha256(settings.installation_id.encode()).hexdigest()
    if manifest["installation_id_sha256"] != installation_digest:
        raise BackupBundleError("backup installation digest does not match runtime")
    if manifest["size_bytes"] != dump_path.stat().st_size or manifest["sha256"] != _sha256(dump_path):
        raise BackupBundleError("backup dump size or SHA-256 does not match manifest")
    snapshot_name = manifest["snapshot_filename"]
    if not isinstance(snapshot_name, str) or Path(snapshot_name).name != snapshot_name:
        raise BackupBundleError("backup snapshot filename is unsafe")
    snapshot_path = dump_path.with_name(snapshot_name)
    _require_regular_file(snapshot_path, "source snapshot")
    if (
        manifest["snapshot_size_bytes"] != snapshot_path.stat().st_size
        or manifest["snapshot_sha256"] != _sha256(snapshot_path)
    ):
        raise BackupBundleError("source snapshot size or SHA-256 does not match manifest")
    namespace_path = dump_path.parent / ".jiuye-namespace.json"
    _verify_backup_namespace(namespace_path)
    if manifest["namespace_sha256"] != _sha256(namespace_path):
        raise BackupBundleError("namespace marker SHA-256 does not match manifest")
    snapshot = _load_snapshot(snapshot_path)
    _assert_snapshot_safe_for_backup(snapshot, require_current=False)
    copied_fields = {
        "snapshot_schema_version": "version",
        "source_build_commit": "build_commit",
        "source_alembic_revision": "alembic_revision",
    }
    for manifest_key, snapshot_key in copied_fields.items():
        if manifest[manifest_key] != snapshot.get(snapshot_key):
            raise BackupBundleError(
                f"manifest {manifest_key} does not match source snapshot"
            )
    if snapshot.get("installation_id_sha256") != installation_digest:
        raise BackupBundleError("source snapshot installation digest does not match")
    return manifest


def _take_snapshot(*, refresh: bool, snapshot_id: str | None) -> dict[str, Any]:
    if snapshot_id and not _SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id):
        raise ValueError("invalid PostgreSQL exported snapshot identifier")
    if refresh:
        if snapshot_id:
            raise ValueError("cannot refresh after importing a read-only snapshot")
        with engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                refresh_derived_relations(connection)
                # The refreshed views and all inventory queries belong to this
                # one stable transaction. Refresh mode is intentionally the only
                # snapshot path that is not read-only.
                return build_operational_snapshot(connection)
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            if snapshot_id:
                # The strict pattern above makes the literal safe; PostgreSQL does not
                # accept a bind parameter in SET TRANSACTION SNAPSHOT.
                connection.execute(text(f"SET TRANSACTION SNAPSHOT '{snapshot_id}'"))
            return build_operational_snapshot(connection)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create/compare PII-free DB snapshots")
    parser.add_argument("--refresh-derived", action="store_true")
    parser.add_argument("--refresh-only", action="store_true")
    parser.add_argument("--snapshot-id")
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--comparison-mode", choices=("strict", "source"), default="strict")
    parser.add_argument("--verify-migration-from", type=Path)
    parser.add_argument("--verify-migration-to", type=Path)
    parser.add_argument("--write-backup-manifest", action="store_true")
    parser.add_argument("--verify-backup-bundle", type=Path)
    parser.add_argument("--dump-file", type=Path)
    parser.add_argument("--snapshot-file", type=Path)
    parser.add_argument("--namespace-file", type=Path)
    parser.add_argument("--manifest-output", type=Path)
    parser.add_argument("--published-dump-name")
    parser.add_argument("--published-snapshot-name")
    args = parser.parse_args()

    try:
        if args.verify_migration_from or args.verify_migration_to:
            if not args.verify_migration_from or not args.verify_migration_to:
                parser.error("migration preservation requires both snapshot paths")
            differences = compare_migration_preservation(
                _load_snapshot(args.verify_migration_from),
                _load_snapshot(args.verify_migration_to),
            )
            if differences:
                for difference in differences:
                    print(f"[migration-mismatch] {difference}", file=sys.stderr)
                return 1
            print("[migration-preservation] common business relations and sequences passed")
            return 0
        if args.verify_backup_bundle:
            verify_backup_bundle(args.verify_backup_bundle)
            print("[backup-bundle] manifest, dump, snapshot, and namespace verified")
            return 0
        if args.write_backup_manifest:
            required = (
                args.dump_file,
                args.snapshot_file,
                args.namespace_file,
                args.manifest_output,
                args.published_dump_name,
                args.published_snapshot_name,
            )
            if any(value is None for value in required):
                parser.error("backup manifest mode requires every bundle path/name argument")
            create_backup_bundle_manifest(
                dump_path=args.dump_file,
                snapshot_path=args.snapshot_file,
                namespace_path=args.namespace_file,
                output_path=args.manifest_output,
                published_dump_name=args.published_dump_name,
                published_snapshot_name=args.published_snapshot_name,
            )
            print("[backup-bundle] manifest created")
            return 0
        if args.refresh_only:
            if args.snapshot_id or args.compare:
                parser.error("--refresh-only cannot be combined with snapshot/compare")
            with engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection:
                with connection.begin():
                    refresh_derived_relations(connection)
            print("[snapshot] derived relations refreshed")
            return 0
        snapshot = _take_snapshot(
            refresh=args.refresh_derived,
            snapshot_id=args.snapshot_id,
        )
        print(json.dumps(snapshot, ensure_ascii=False, sort_keys=True))
        if args.compare:
            differences = compare_operational_snapshots(
                _load_snapshot(args.compare),
                snapshot,
                mode=args.comparison_mode,
            )
            if differences:
                for difference in differences:
                    print(f"[snapshot-mismatch] {difference}", file=sys.stderr)
                return 1
        return 0
    except (BackupBundleError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[snapshot-error] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
