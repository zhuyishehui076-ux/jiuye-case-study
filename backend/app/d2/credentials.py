"""CLI coordinator for atomic, recoverable Demo credential releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from app.d2.credential_release import (
    DemoCredentialCandidate,
    DemoCredentialReleaseError,
    _fsync_directory,
    _read_protected_json,
    _validate_secret_path,
    abort_demo_credential_release,
    atomic_write_protected_json,
    demo_credential_release_status,
    demo_credential_release_status_or_none,
    finalize_demo_credential_release,
    handoff_fingerprint_from_file,
    inflight_demo_credential_release,
    load_candidate_file,
    load_handoff_candidate,
    mark_demo_credential_release_verified,
    prepare_candidate_file,
    promote_demo_credential_release,
    record_demo_credential_handoff,
    remove_protected_file,
    stage_demo_credential_release,
)
from app.db.session import SessionLocal
from app.seed.users import DEMO_ACCOUNTS


class CredentialSmokeError(DemoCredentialReleaseError):
    """HTTP verification error that deliberately omits response bodies."""


def _request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=body,
        headers=headers or {},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except (OSError, urllib.error.URLError) as exc:
        raise CredentialSmokeError(f"凭据验证端点不可达：{path}") from exc
    try:
        payload = json.loads(raw) if raw else {}
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CredentialSmokeError(f"凭据验证端点返回格式无效：{path}") from exc
    if not isinstance(payload, dict):
        raise CredentialSmokeError(f"凭据验证端点返回对象无效：{path}")
    return status, payload


def _require_status(status: int, expected: int, *, label: str) -> None:
    if status != expected:
        raise CredentialSmokeError(
            f"{label} 验证失败：HTTP {status}，期望 HTTP {expected}。"
        )


def _management_login(
    base_url: str, *, username: str, password: str
) -> str:
    body = urllib.parse.urlencode(
        {"username": username, "password": password}
    ).encode("utf-8")
    status, payload = _request(
        base_url,
        "/api/v1/auth/login",
        method="POST",
        body=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    _require_status(status, 200, label=f"管理账号 {username} 登录")
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise CredentialSmokeError(f"管理账号 {username} 未返回访问令牌。")
    status, _ = _request(
        base_url,
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    _require_status(status, 200, label=f"管理账号 {username} 授权")
    return token


def _student_login(
    base_url: str, *, binding_code: str
) -> tuple[str, dict[str, Any]]:
    body = json.dumps(
        {"wx_code": "credential-release-smoke", "binding_code": binding_code},
        ensure_ascii=False,
    ).encode("utf-8")
    status, payload = _request(
        base_url,
        "/api/v1/student/auth/login",
        method="POST",
        body=body,
        headers={"Content-Type": "application/json"},
    )
    _require_status(status, 200, label="学生端登录")
    token = payload.get("access_token")
    student_id = payload.get("student_id")
    if not isinstance(token, str) or not isinstance(student_id, int):
        raise CredentialSmokeError("学生端登录未返回有效身份。")
    status, _ = _request(
        base_url,
        f"/api/v1/student/students/{student_id}/growth",
        headers={"Authorization": f"Bearer {token}"},
    )
    _require_status(status, 200, label="学生端本人数据授权")
    return token, payload


def _enterprise_login(base_url: str, *, enterprise_token: str) -> dict[str, Any]:
    status, payload = _request(
        base_url,
        "/api/v1/enterprise/session",
        headers={"X-Enterprise-Token": enterprise_token},
    )
    _require_status(status, 200, label="企业端登录")
    return payload


def _smoke_candidate(
    base_url: str, candidate: DemoCredentialCandidate
) -> tuple[dict[str, Any], dict[str, str]]:
    issued_tokens = {
        username: _management_login(
            base_url,
            username=username,
            password=candidate.management_accounts[username],
        )
        for username in sorted(candidate.management_accounts)
    }
    student_token, student = _student_login(
        base_url, binding_code=candidate.student_binding_code
    )
    enterprise = _enterprise_login(
        base_url, enterprise_token=candidate.enterprise_token
    )
    metadata = {
        "student_id": student.get("student_id"),
        "student_no": student.get("student_no"),
        "student_name": student.get("name"),
        "enterprise_company": enterprise.get("company"),
        "enterprise_expires_at": enterprise.get("expires_at"),
    }
    return metadata, {**issued_tokens, "student": student_token}


def _capture_previous_sessions(
    base_url: str, handoff_path: Path, *, next_release_id: str
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    if not handoff_path.exists() or handoff_path.is_symlink():
        return None, {}
    previous = _read_protected_json(handoff_path)
    if previous.get("release_id") == next_release_id:
        # A retry after promotion reads the already-promoted handoff.  Those
        # credentials are the current release, not the previous one that must
        # be proved revoked.
        return None, {}
    sessions: dict[str, str] = {}
    accounts = previous.get("management_accounts")
    if isinstance(accounts, dict):
        for username, entry in accounts.items():
            password = entry.get("password") if isinstance(entry, dict) else None
            if username in DEMO_ACCOUNTS and isinstance(password, str):
                try:
                    sessions[username] = _management_login(
                        base_url, username=username, password=password
                    )
                except CredentialSmokeError:
                    pass
    binding_code = previous.get("student_binding_code")
    if isinstance(binding_code, str):
        try:
            sessions["student"], _ = _student_login(
                base_url, binding_code=binding_code
            )
        except CredentialSmokeError:
            pass
    return previous, sessions


def _previous_proof_path(handoff_path: Path) -> Path:
    return handoff_path.parent / f".{handoff_path.name}.previous"


def _load_previous_proof(
    proof_path: Path, *, next_release_id: str
) -> tuple[dict[str, Any], dict[str, str]]:
    payload = _read_protected_json(proof_path)
    if payload.get("schema_version") != 1 or payload.get(
        "next_release_id"
    ) != next_release_id:
        raise DemoCredentialReleaseError(
            "Previous credential proof is not bound to this release."
        )
    previous = payload.get("previous_handoff")
    sessions = payload.get("sessions")
    if not isinstance(previous, dict) or not isinstance(sessions, dict):
        raise DemoCredentialReleaseError("Previous credential proof is invalid.")
    if any(
        key not in {*DEMO_ACCOUNTS, "student"} or not isinstance(value, str)
        for key, value in sessions.items()
    ):
        raise DemoCredentialReleaseError("Previous session proof is invalid.")
    return previous, dict(sessions)


def _capture_or_load_previous_proof(
    base_url: str,
    handoff_path: Path,
    *,
    next_release_id: str,
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    proof_path = _previous_proof_path(handoff_path)
    if proof_path.exists() or proof_path.is_symlink():
        return _load_previous_proof(
            proof_path, next_release_id=next_release_id
        )
    previous, sessions = _capture_previous_sessions(
        base_url, handoff_path, next_release_id=next_release_id
    )
    if previous is not None:
        atomic_write_protected_json(
            proof_path,
            {
                "schema_version": 1,
                "next_release_id": next_release_id,
                "previous_handoff": previous,
                "sessions": sessions,
            },
            replace=False,
        )
    return previous, sessions


def _remove_previous_proof_for_release(
    handoff_path: Path, *, release_id: str
) -> None:
    proof_path = _previous_proof_path(handoff_path)
    if not proof_path.exists() and not proof_path.is_symlink():
        return
    _load_previous_proof(proof_path, next_release_id=release_id)
    remove_protected_file(proof_path)


def _handoff_payload(
    candidate: DemoCredentialCandidate, metadata: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "release_id": candidate.release_id,
        "generated_at": candidate.generated_at,
        "activated_at": candidate.generated_at,
        "candidate_fingerprint": candidate.fingerprint,
        "management_accounts": {
            username: {
                "display_name": DEMO_ACCOUNTS[username][0],
                "password": candidate.management_accounts[username],
            }
            for username in sorted(candidate.management_accounts)
        },
        "student_binding_code": candidate.student_binding_code,
        "enterprise_token": candidate.enterprise_token,
        **metadata,
    }


def _payload_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _candidate_from_handoff_file(path: Path) -> DemoCredentialCandidate:
    return load_handoff_candidate(path)


def _load_candidate_or_recovery(
    candidate_path: Path, handoff_path: Path
) -> DemoCredentialCandidate:
    if candidate_path.exists() or candidate_path.is_symlink():
        return load_candidate_file(candidate_path)
    next_path = handoff_path.parent / f".{handoff_path.name}.next"
    with SessionLocal() as db:
        inflight = inflight_demo_credential_release(db)
    for recovery_path in (next_path, handoff_path):
        if not recovery_path.exists() and not recovery_path.is_symlink():
            continue
        candidate = _candidate_from_handoff_file(recovery_path)
        if inflight is not None and candidate.release_id != inflight[0]:
            continue
        with SessionLocal() as db:
            status = demo_credential_release_status(db, candidate)
        if status not in {"promoting", "active"} and not (
            status == "verified" and recovery_path == next_path
        ):
            continue
        return candidate
    if inflight is not None:
        raise DemoCredentialReleaseError(
            f"未完成候选 {inflight[0]} 缺少可恢复文件；请显式中止或恢复受保护文件。"
        )
    raise DemoCredentialReleaseError("候选凭据文件不存在且没有可恢复交接文件。")


def _replace_handoff(next_path: Path, handoff_path: Path) -> None:
    _validate_secret_path(next_path)
    _validate_secret_path(handoff_path)
    if not next_path.is_file() or next_path.is_symlink():
        raise DemoCredentialReleaseError("待提升交接文件不存在或不安全。")
    if handoff_path.is_symlink():
        raise DemoCredentialReleaseError("交接文件不得是符号链接。")
    try:
        os.replace(next_path, handoff_path)
        os.chmod(handoff_path, 0o600)
        _fsync_directory(handoff_path.parent)
    except OSError as exc:
        raise DemoCredentialReleaseError("交接文件原子提升失败。") from exc


def _assert_revoked(
    base_url: str,
    previous: dict[str, Any] | None,
    previous_sessions: dict[str, str],
) -> None:
    for username, token in previous_sessions.items():
        path = (
            "/api/v1/auth/me"
            if username != "student"
            else (
                "/api/v1/student/students/"
                f"{previous.get('student_id') if previous else 0}/growth"
            )
        )
        status, _ = _request(
            base_url, path, headers={"Authorization": f"Bearer {token}"}
        )
        _require_status(status, 401, label=f"旧 {username} 会话失效")
    if previous is None:
        return
    accounts = previous.get("management_accounts")
    if isinstance(accounts, dict):
        for username, entry in accounts.items():
            password = entry.get("password") if isinstance(entry, dict) else None
            if username not in DEMO_ACCOUNTS or not isinstance(password, str):
                continue
            body = urllib.parse.urlencode(
                {"username": username, "password": password}
            ).encode("utf-8")
            status, _ = _request(
                base_url,
                "/api/v1/auth/login",
                method="POST",
                body=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            _require_status(status, 401, label=f"旧管理口令 {username} 失效")
    binding_code = previous.get("student_binding_code")
    if isinstance(binding_code, str):
        body = json.dumps(
            {"wx_code": "credential-revocation", "binding_code": binding_code}
        ).encode("utf-8")
        status, _ = _request(
            base_url,
            "/api/v1/student/auth/login",
            method="POST",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        _require_status(status, 401, label="旧学生绑定码失效")
    enterprise_token = previous.get("enterprise_token")
    if isinstance(enterprise_token, str):
        status, _ = _request(
            base_url,
            "/api/v1/enterprise/session",
            headers={"X-Enterprise-Token": enterprise_token},
        )
        _require_status(status, 401, label="旧企业令牌失效")


def activate_release(
    *, candidate_path: Path, handoff_path: Path, base_url: str
) -> str:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DemoCredentialReleaseError("验证 base URL 无效。")
    candidate = _load_candidate_or_recovery(candidate_path, handoff_path)
    with SessionLocal() as db:
        initial_status = demo_credential_release_status(db, candidate)
    metadata, candidate_sessions = _smoke_candidate(base_url, candidate)
    previous, previous_sessions = _capture_or_load_previous_proof(
        base_url,
        handoff_path,
        next_release_id=candidate.release_id,
    )
    with SessionLocal() as db:
        mark_demo_credential_release_verified(db, candidate)
    next_path = handoff_path.parent / f".{handoff_path.name}.next"
    handoff_payload = _handoff_payload(candidate, metadata)
    handoff_fingerprint = _payload_fingerprint(handoff_payload)
    atomic_write_protected_json(
        next_path, handoff_payload, replace=True
    )
    with SessionLocal() as db:
        record_demo_credential_handoff(
            db,
            candidate,
            handoff_path=next_path,
        )
    with SessionLocal() as db:
        release = promote_demo_credential_release(
            db, candidate, handoff_path=next_path
        )
    _replace_handoff(next_path, handoff_path)
    if handoff_fingerprint_from_file(handoff_path, candidate) != handoff_fingerprint:
        raise DemoCredentialReleaseError("原子提升后的交接文件指纹不一致。")
    if initial_status in {"staged", "verified"}:
        for username, token in candidate_sessions.items():
            path = (
                "/api/v1/auth/me"
                if username != "student"
                else f"/api/v1/student/students/{metadata['student_id']}/growth"
            )
            status, _ = _request(
                base_url, path, headers={"Authorization": f"Bearer {token}"}
            )
            _require_status(status, 401, label=f"提升前 {username} 会话失效")
    _smoke_candidate(base_url, candidate)
    _assert_revoked(base_url, previous, previous_sessions)
    with SessionLocal() as db:
        release = finalize_demo_credential_release(
            db,
            candidate,
            handoff_path=handoff_path,
        )
    remove_protected_file(candidate_path)
    remove_protected_file(next_path)
    remove_protected_file(_previous_proof_path(handoff_path))
    return release.status


def _path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("凭据路径必须是绝对路径")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.d2.credentials")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--candidate-file", required=True, type=_path)
    stage = subparsers.add_parser("stage")
    stage.add_argument("--candidate-file", required=True, type=_path)
    activate = subparsers.add_parser("activate")
    activate.add_argument("--candidate-file", required=True, type=_path)
    activate.add_argument("--handoff-file", required=True, type=_path)
    activate.add_argument("--base-url", required=True)
    abort = subparsers.add_parser("abort")
    abort.add_argument("--release-id", required=True)
    abort.add_argument("--candidate-file", required=True, type=_path)
    abort.add_argument("--handoff-file", required=True, type=_path)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            if args.candidate_file.exists() or args.candidate_file.is_symlink():
                existing_candidate = load_candidate_file(args.candidate_file)
                with SessionLocal() as db:
                    existing_status = demo_credential_release_status_or_none(
                        db, existing_candidate
                    )
                if existing_status == "aborted":
                    remove_protected_file(args.candidate_file)
            if not args.candidate_file.exists():
                with SessionLocal() as db:
                    inflight = inflight_demo_credential_release(db)
                if inflight is not None:
                    raise DemoCredentialReleaseError(
                        f"未完成候选 {inflight[0]} 仍为 {inflight[1]}；拒绝生成新值。"
                    )
            candidate = prepare_candidate_file(args.candidate_file)
            status = "prepared"
        elif args.command == "stage":
            candidate = load_candidate_file(args.candidate_file)
            with SessionLocal() as db:
                release = stage_demo_credential_release(db, candidate)
                status = release.status
        elif args.command == "activate":
            candidate = _load_candidate_or_recovery(
                args.candidate_file, args.handoff_file
            )
            status = activate_release(
                candidate_path=args.candidate_file,
                handoff_path=args.handoff_file,
                base_url=args.base_url,
            )
        else:
            if not re.fullmatch(r"demo-[0-9a-f]{32}", args.release_id):
                raise DemoCredentialReleaseError("候选 release ID 无效。")
            if args.candidate_file.exists() or args.candidate_file.is_symlink():
                candidate_to_abort = load_candidate_file(args.candidate_file)
                if candidate_to_abort.release_id != args.release_id:
                    raise DemoCredentialReleaseError(
                        "Candidate file does not match the release being aborted."
                    )
            with SessionLocal() as db:
                abort_demo_credential_release(db, release_id=args.release_id)
            remove_protected_file(args.candidate_file)
            _remove_previous_proof_for_release(
                args.handoff_file, release_id=args.release_id
            )
            print(
                json.dumps(
                    {"release_id": args.release_id, "status": "aborted"},
                    ensure_ascii=False,
                )
            )
            return 0
    except DemoCredentialReleaseError as exc:
        print(f"[demo-credentials] {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"release_id": candidate.release_id, "status": status},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
