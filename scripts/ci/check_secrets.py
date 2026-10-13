#!/usr/bin/env python3
"""Fail-closed high-confidence secret scanner for source and release artifacts.

The scanner never prints matched values.  It scans every Docker image layer,
including values later removed by a whiteout, when ``--image`` is used.
"""

from __future__ import annotations

import argparse
import base64
import bz2
import gzip
import hashlib
import io
import json
import lzma
import math
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable

ROOT = Path(__file__).resolve().parents[2]
MAX_TEXT_BYTES = 32 * 1024 * 1024
MAX_ZIP_MEMBER_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 6
STREAM_CHUNK_BYTES = 8 * 1024 * 1024
STREAM_OVERLAP_BYTES = 64 * 1024
ZIP_EOCD_SEARCH_BYTES = 65_557

ARCHIVE_SUFFIXES = (
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.xz",
    ".txz",
    ".tar.bz2",
    ".tbz",
    ".tbz2",
    ".tar.zst",
    ".tzst",
    ".zip",
)
DENIED_FILE_NAMES = {
    ".env",
    ".netrc",
    ".npmrc",
    "cookies",
    "cookies.sqlite",
    "credentials.json",
    "service-account.json",
}
DENIED_SUFFIXES = {".key", ".p12", ".pfx", ".jks", ".kdbx"}
DENIED_DIRECTORY_NAMES = {
    ".playwright-cli",
    "browser_profiles",
    "credentials",
    "secrets",
    "session storage",
}
FORBIDDEN_DEMO_SECRET_HASHES = {
    hashlib.sha256((role + str(100 + 23)).encode()).hexdigest()
    for role in ("admin", "office", "counselor", "leader")
}


@dataclass(frozen=True, order=True)
class Finding:
    rule: str
    path: str
    line: int
    value_hash: str

    def render(self) -> str:
        return (
            f"[secret-scan] {self.rule} {self.path}:{self.line} "
            f"sha256={self.value_hash}"
        )


class _PrefixedReader:
    def __init__(self, prefix: bytes, stream: BinaryIO) -> None:
        self._prefix = io.BytesIO(prefix)
        self._stream = stream

    def read(self, size: int = -1) -> bytes:
        first = self._prefix.read(size)
        if size < 0:
            return first + self._stream.read()
        if len(first) == size:
            return first
        return first + self._stream.read(size - len(first))


def _digest(value: bytes | str) -> str:
    raw = value.encode("utf-8", errors="replace") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()[:16]


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {char: value.count(char) for char in set(value)}
    size = len(value)
    return -sum((count / size) * math.log2(count / size) for count in counts.values())


def _path_findings(display_path: str) -> list[Finding]:
    normalized = display_path.replace("\\", "/")
    archive_leaf = normalized.rsplit("!", 1)[-1]
    pure = PurePosixPath(archive_leaf)
    lowered_parts = {part.lower() for part in pure.parts}
    name = pure.name.lower()
    findings: list[Finding] = []
    if (
        name in DENIED_FILE_NAMES
        or pure.suffix.lower() in DENIED_SUFFIXES
        or any(part in DENIED_DIRECTORY_NAMES for part in lowered_parts)
        or (name.startswith("credentials") and name.endswith(".json"))
        or ("cookie" in name and any(part in {"default", "profile"} for part in lowered_parts))
    ):
        findings.append(Finding("SECRET_PATH", normalized, 1, _digest(normalized)))
    return findings


PRIVATE_KEY_PREFIX_RE = re.compile(
    rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"
    rb"[ \t]*\r?\n[A-Za-z0-9+/=]{32,}"
)
PROVIDER_TOKEN_RES = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bLTAI[A-Za-z0-9]{12,30}\b"),
    re.compile(r"\bAKID[A-Za-z0-9]{13,40}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,255}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,255}\b"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,255}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,255}\b"),
)
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
BEARER_RE = re.compile(r"(?i)\bBearer\s+([A-Za-z0-9._~+/-]{24,})")
URL_CREDENTIAL_RE = re.compile(
    r"(?i)\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis)://"
    r"[^\s:/@]+:([^\s/@]{12,})@"
)
ASSIGNMENT_RE = re.compile(
    r"(?im)(?:^|[,{\s])"
    r"(?:password|passwd|secret|api[_-]?key|access[_-]?key|token|"
    r"口令|密码|密钥|令牌|绑定码)"
    r"\s*[=:]\s*[\"']([A-Za-z0-9_./+~!@#$%^&*()=-]{16,})[\"']"
)
FRONTEND_SECRET_RE = re.compile(
    r"(?im)\b(?:VITE|TARO_APP)_[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|API_KEY)"
    r"\s*[=:]\s*[\"']?([^\s\"',}]{1,})"
)
WORD_RE = re.compile(r"[A-Za-z0-9_!@#$%^&*+=.-]{6,128}")
PLACEHOLDER_MARKERS = ("example", "placeholder", "replace", "changeme", "<", "${")


def _scan_text(display_path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []

    def add(rule: str, match: re.Match[str], value: str | None = None) -> None:
        raw = value if value is not None else match.group(0)
        findings.append(
            Finding(rule, display_path, _line(text, match.start()), _digest(raw))
        )

    for pattern in PROVIDER_TOKEN_RES:
        for match in pattern.finditer(text):
            add("PROVIDER_TOKEN", match)
    for match in JWT_RE.finditer(text):
        try:
            payload_part = match.group(0).split(".")[1]
            payload = json.loads(
                base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4))
            )
        except (UnicodeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and ({"iat", "exp"} & set(payload)):
            add("JWT", match)
    for match in BEARER_RE.finditer(text):
        add("BEARER_TOKEN", match, match.group(1))
    for match in URL_CREDENTIAL_RE.finditer(text):
        password = match.group(1)
        if _entropy(password) >= 3.0:
            add("CREDENTIAL_URL", match, password)
    for match in FRONTEND_SECRET_RE.finditer(text):
        value = match.group(1)
        if not any(marker in value.lower() for marker in PLACEHOLDER_MARKERS):
            add("FRONTEND_SECRET", match, value)
    for match in ASSIGNMENT_RE.finditer(text):
        value = match.group(1)
        lowered = value.lower()
        if (
            _entropy(value) >= 3.5
            and not any(marker in lowered for marker in PLACEHOLDER_MARKERS)
        ):
            add("HIGH_ENTROPY_ASSIGNMENT", match, value)
    for match in WORD_RE.finditer(text):
        if hashlib.sha256(match.group(0).encode()).hexdigest() in FORBIDDEN_DEMO_SECRET_HASHES:
            add("HISTORICAL_DEMO_SECRET", match)
    return findings


def _decode_text(data: bytes) -> str | None:
    if not data:
        return ""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeError:
            return None
    if b"\x00" in data[:8192]:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        return None
    control = sum(ord(char) < 9 or 13 < ord(char) < 32 for char in text[:8192])
    if control > max(4, len(text[:8192]) // 20):
        return None
    return text


def _scan_content_bytes(display_path: str, data: bytes) -> list[Finding]:
    findings: list[Finding] = []
    # The header plus the start of a base64 body is high confidence while not
    # flagging serialization libraries that merely contain PEM marker strings.
    # No end marker is required, so an oversized key cannot span the overlap.
    for match in PRIVATE_KEY_PREFIX_RE.finditer(data):
        findings.append(
            Finding(
                "PRIVATE_KEY",
                display_path,
                data.count(b"\n", 0, match.start()) + 1,
                _digest(match.group(0)),
            )
        )
    text = _decode_text(data)
    if text is None:
        printable = b"\n".join(re.findall(rb"[\x20-\x7e]{12,}", data))
        text = printable.decode("ascii", errors="ignore")
    findings.extend(_scan_text(display_path, text))
    return findings


def _scan_bytes(display_path: str, data: bytes) -> list[Finding]:
    return _path_findings(display_path) + _scan_content_bytes(display_path, data)


def _scan_stream(
    display_path: str,
    stream: BinaryIO,
    size: int,
    *,
    detect_opaque_zip: bool = False,
    nonzero_tail_after: int | None = None,
) -> list[Finding]:
    """Scan a complete stream without trusting an early binary-looking prefix."""
    if size <= MAX_TEXT_BYTES:
        data = stream.read(MAX_TEXT_BYTES + 1)
        if len(data) > MAX_TEXT_BYTES:
            return [
                Finding(
                    "UNSCANNABLE_FILE",
                    display_path,
                    1,
                    _digest(display_path),
                )
            ]
        findings = _scan_bytes(display_path, data)
        if nonzero_tail_after is not None and any(data[nonzero_tail_after:]):
            findings.append(
                Finding(
                    "ARCHIVE_TRAILING_DATA",
                    display_path,
                    1,
                    _digest(display_path),
                )
            )
        return findings

    findings = _path_findings(display_path)
    tail = b""
    lines_before_chunk = 0
    bytes_before_chunk = 0
    trailing_data_found = False
    while True:
        chunk = stream.read(STREAM_CHUNK_BYTES)
        if not chunk:
            break
        if (
            nonzero_tail_after is not None
            and not trailing_data_found
            and bytes_before_chunk + len(chunk) > nonzero_tail_after
        ):
            tail_start = max(0, nonzero_tail_after - bytes_before_chunk)
            trailing_data_found = any(chunk[tail_start:])
        window = tail + chunk
        window_start_line = 1 + lines_before_chunk - tail.count(b"\n")
        for finding in _scan_content_bytes(display_path, window):
            findings.append(
                Finding(
                    finding.rule,
                    finding.path,
                    window_start_line + finding.line - 1,
                    finding.value_hash,
                )
            )
        lines_before_chunk += chunk.count(b"\n")
        bytes_before_chunk += len(chunk)
        tail = window[-max(STREAM_OVERLAP_BYTES, ZIP_EOCD_SEARCH_BYTES) :]
    if trailing_data_found:
        findings.append(
            Finding(
                "ARCHIVE_TRAILING_DATA",
                display_path,
                1,
                _digest(display_path),
            )
        )
    if detect_opaque_zip and _has_structural_zip_eocd(tail):
        findings.append(
            Finding("UNSCANNABLE_ARCHIVE", display_path, 1, _digest(display_path))
        )
    return findings


def _is_archive_name(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(ARCHIVE_SUFFIXES)


def _archive_kind(name: str, prefix: bytes) -> str | None:
    lowered = name.lower()
    if lowered.endswith(".zip") or prefix.startswith(b"PK\x03\x04"):
        return "zip"
    if prefix.startswith(b"\x1f\x8b"):
        return "gzip"
    if prefix.startswith(b"\xfd7zXZ\x00"):
        return "xz"
    if prefix.startswith(b"BZh"):
        return "bzip2"
    if prefix.startswith(b"\x28\xb5\x2f\xfd") or (
        len(prefix) >= 4
        and 0x184D2A50 <= int.from_bytes(prefix[:4], "little") <= 0x184D2A5F
    ):
        return "zstd"
    if lowered.endswith(
        (
            ".tar",
            ".tar.gz",
            ".tgz",
            ".tar.xz",
            ".txz",
            ".tar.bz2",
            ".tbz",
            ".tbz2",
            ".tar.zst",
            ".tzst",
        )
    ) or (len(prefix) > 262 and prefix[257:262] == b"ustar"):
        return "tar"
    return None


def _safe_member_name(name: str, *, allow_root: bool = False) -> str:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        raise ValueError("archive member path traversal")
    parts: list[str] = []
    for part in normalized.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise ValueError("archive member path traversal")
        parts.append(part)
    if not parts:
        if allow_root:
            return "."
        raise ValueError("archive member path traversal")
    return "/".join(parts)


def _decompression_reader(source: BinaryIO, kind: str) -> BinaryIO:
    if kind == "gzip":
        return gzip.GzipFile(fileobj=source)
    if kind == "xz":
        return lzma.LZMAFile(source)
    if kind == "bzip2":
        return bz2.BZ2File(source)
    raise ValueError("unsupported compressed stream")


def _decompress_limited(payload: bytes, kind: str) -> bytes:
    source = io.BytesIO(payload)
    stream = _decompression_reader(source, kind)
    with stream:
        decompressed = stream.read(MAX_ZIP_MEMBER_BYTES + 1)
    if len(decompressed) > MAX_ZIP_MEMBER_BYTES:
        raise ValueError("decompressed stream exceeds scan limit")
    return decompressed


def _scan_nested_bytes(
    data: bytes,
    display_path: str,
    *,
    depth: int,
) -> list[Finding]:
    if depth > MAX_ARCHIVE_DEPTH:
        return [Finding("ARCHIVE_DEPTH", display_path, 1, _digest(display_path))]
    kind = _archive_kind(display_path.rsplit("!", 1)[-1], data[:512])
    if kind is None and zipfile.is_zipfile(io.BytesIO(data)):
        kind = "zip"
    raw_findings = (
        _scan_content_bytes(
            f"{display_path}!"
            + (
                "compressed-metadata"
                if kind in {"gzip", "xz", "bzip2", "zstd"}
                else "archive-raw"
            ),
            data,
        )
        if kind is not None
        else []
    )
    try:
        if kind == "zip":
            findings = raw_findings + _scan_zip_bytes(
                data,
                display_path,
                depth=depth,
            )
            zip_end = _zip_eocd_end(data)
            if zip_end is None:
                raise ValueError("ZIP end-of-central-directory not found")
            if data[zip_end:]:
                findings.append(
                    Finding(
                        "ARCHIVE_TRAILING_DATA",
                        display_path,
                        1,
                        _digest(display_path),
                    )
                )
            return findings
        if kind == "tar":
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as nested:
                findings = raw_findings + _scan_tar(
                    nested,
                    display_path,
                    depth=depth,
                )
                tar_end = nested.offset
            if any(data[tar_end:]):
                findings.append(
                    Finding(
                        "ARCHIVE_TRAILING_DATA",
                        display_path,
                        1,
                        _digest(display_path),
                    )
                )
            return findings
        if kind in {"gzip", "xz", "bzip2", "zstd"}:
            decompressed = _decompress_limited(data, kind)
            return raw_findings + _scan_nested_bytes(
                decompressed,
                f"{display_path}!decompressed-stream",
                depth=depth + 1,
            )
    except (OSError, EOFError, lzma.LZMAError, tarfile.TarError, zipfile.BadZipFile, ValueError):
        return raw_findings + [
            Finding("UNSCANNABLE_ARCHIVE", display_path, 1, _digest(display_path))
        ]
    return _scan_bytes(display_path, data)


def _has_structural_zip_eocd(tail: bytes) -> bool:
    return _zip_eocd_end(tail) == len(tail)


def _zip_eocd_end(data: bytes) -> int | None:
    marker = b"PK\x05\x06"
    offset = data.rfind(marker)
    while offset >= 0:
        if len(data) - offset >= 22:
            comment_size = int.from_bytes(data[offset + 20 : offset + 22], "little")
            end = offset + 22 + comment_size
            if end <= len(data):
                return end
        offset = data.rfind(marker, 0, offset)
    return None


def _zip_path_has_trailing_data(path: Path) -> bool:
    size = path.stat().st_size
    window_size = min(size, ZIP_EOCD_SEARCH_BYTES * 2)
    with path.open("rb") as stream:
        stream.seek(size - window_size)
        tail = stream.read(window_size)
    zip_end = _zip_eocd_end(tail)
    return zip_end is None or size - window_size + zip_end != size


def _path_has_nonzero_bytes_after(path: Path, offset: int) -> bool:
    with path.open("rb") as stream:
        stream.seek(offset)
        while True:
            chunk = stream.read(STREAM_CHUNK_BYTES)
            if not chunk:
                return False
            if any(chunk):
                return True


def _scan_spooled_archive(
    stream: BinaryIO,
    size: int,
    display_path: str,
    *,
    depth: int,
) -> list[Finding]:
    """Replay a nested archive without bounding its decompressed size."""
    with tempfile.TemporaryDirectory(prefix="jiuye-secret-nested-") as temp_dir:
        payload_path = Path(temp_dir) / "payload.bin"
        remaining = size
        with payload_path.open("wb") as target:
            while remaining:
                chunk = stream.read(min(STREAM_CHUNK_BYTES, remaining))
                if not chunk:
                    return [
                        Finding(
                            "UNSCANNABLE_ARCHIVE",
                            display_path,
                            1,
                            _digest(display_path),
                        )
                    ]
                target.write(chunk)
                remaining -= len(chunk)
        return scan_path(payload_path, display_path=display_path, depth=depth)


def _scan_tar(
    archive: tarfile.TarFile, display_path: str, *, depth: int
) -> list[Finding]:
    if depth > MAX_ARCHIVE_DEPTH:
        return [Finding("ARCHIVE_DEPTH", display_path, 1, _digest(display_path))]
    findings: list[Finding] = []
    global_pax = getattr(archive, "pax_headers", {})
    if global_pax:
        findings.extend(
            _scan_content_bytes(
                f"{display_path}!tar-global-metadata",
                json.dumps(
                    global_pax,
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8", errors="replace"),
            )
        )
    for member in archive:
        try:
            name = _safe_member_name(member.name, allow_root=member.isdir())
        except ValueError:
            findings.append(
                Finding("ARCHIVE_PATH_TRAVERSAL", display_path, 1, _digest(member.name))
            )
            continue
        metadata_display = f"{display_path}!tar-member-metadata-{_digest(name)}"
        metadata = b"\n".join(
            (
                member.name.encode("utf-8", errors="replace"),
                member.linkname.encode("utf-8", errors="replace"),
                json.dumps(
                    member.pax_headers,
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8", errors="replace"),
            )
        )
        metadata_hits = _scan_content_bytes(metadata_display, metadata)
        findings.extend(metadata_hits)
        nested_display = (
            metadata_display if metadata_hits else f"{display_path}!{name}"
        )
        for path_finding in _path_findings(f"{display_path}!{name}"):
            findings.append(
                Finding(
                    path_finding.rule,
                    nested_display,
                    path_finding.line,
                    path_finding.value_hash,
                )
            )
        if not member.isfile():
            continue
        stream = archive.extractfile(member)
        if stream is None:
            continue
        prefix = stream.read(512)
        prefixed = _PrefixedReader(prefix, stream)
        archive_kind = _archive_kind(name, prefix)
        if archive_kind is not None:
            findings.extend(
                _scan_spooled_archive(
                    prefixed,
                    member.size,
                    nested_display,
                    depth=depth + 1,
                )
            )
            continue
        if member.size <= MAX_ZIP_MEMBER_BYTES:
            payload = prefixed.read()
            findings.extend(
                _scan_nested_bytes(payload, nested_display, depth=depth + 1)
            )
            continue
        findings.extend(
            _scan_stream(
                nested_display,
                prefixed,
                member.size,
                detect_opaque_zip=True,
            )
        )
    return findings


def _scan_zip_archive(
    archive: zipfile.ZipFile,
    display_path: str,
    *,
    depth: int,
) -> list[Finding]:
    if depth > MAX_ARCHIVE_DEPTH:
        return [Finding("ARCHIVE_DEPTH", display_path, 1, _digest(display_path))]
    findings: list[Finding] = []
    if archive.comment:
        findings.extend(
            _scan_content_bytes(
                f"{display_path}!zip-archive-metadata",
                archive.comment,
            )
        )
    for member in archive.infolist():
        if member.is_dir():
            continue
        try:
            name = _safe_member_name(member.filename)
        except ValueError:
            findings.append(
                Finding("ARCHIVE_PATH_TRAVERSAL", display_path, 1, _digest(member.filename))
            )
            continue
        metadata_display = f"{display_path}!zip-member-metadata-{_digest(name)}"
        metadata = b"\n".join(
            (
                member.filename.encode("utf-8", errors="replace"),
                member.comment,
                member.extra,
            )
        )
        metadata_hits = _scan_content_bytes(metadata_display, metadata)
        findings.extend(metadata_hits)
        nested_display = (
            metadata_display if metadata_hits else f"{display_path}!{name}"
        )
        for path_finding in _path_findings(f"{display_path}!{name}"):
            findings.append(
                Finding(
                    path_finding.rule,
                    nested_display,
                    path_finding.line,
                    path_finding.value_hash,
                )
            )
        if member.file_size > MAX_ZIP_MEMBER_BYTES:
            findings.append(
                Finding("UNSCANNABLE_ARCHIVE", nested_display, 1, _digest(nested_display))
            )
            continue
        payload = archive.read(member)
        findings.extend(
            _scan_nested_bytes(payload, nested_display, depth=depth + 1)
        )
    return findings


def _scan_zip_bytes(data: bytes, display_path: str, *, depth: int) -> list[Finding]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return _scan_zip_archive(archive, display_path, depth=depth)


def scan_path(
    path: Path,
    *,
    display_path: str | None = None,
    depth: int = 1,
) -> list[Finding]:
    display = display_path or path.as_posix()
    if path.is_symlink():
        return [Finding("SYMLINK_INPUT", display, 1, _digest(display))]
    if path.is_dir():
        findings: list[Finding] = []
        for child in sorted(item for item in path.rglob("*") if item.is_file() or item.is_symlink()):
            findings.extend(
                scan_path(
                    child,
                    display_path=child.as_posix(),
                    depth=depth,
                )
            )
        return findings
    findings = _path_findings(display)
    try:
        is_zip = zipfile.is_zipfile(path)
        with path.open("rb") as stream:
            prefix = stream.read(512)
    except OSError:
        return findings + [Finding("UNSCANNABLE_FILE", display, 1, _digest(display))]
    archive_kind = _archive_kind(path.name, prefix)
    archive_candidate = is_zip or archive_kind is not None or _is_archive_name(path.name)
    if archive_candidate:
        with path.open("rb") as stream:
            findings.extend(
                _scan_stream(
                    f"{display}!"
                    + (
                        "compressed-metadata"
                        if archive_kind in {"gzip", "xz", "bzip2", "zstd"}
                        else "archive-raw"
                    ),
                    stream,
                    path.stat().st_size,
                )
            )
    if is_zip or archive_kind == "zip":
        try:
            with zipfile.ZipFile(path) as archive:
                findings.extend(_scan_zip_archive(archive, display, depth=depth))
            if _zip_path_has_trailing_data(path):
                findings.append(
                    Finding(
                        "ARCHIVE_TRAILING_DATA",
                        display,
                        1,
                        _digest(display),
                    )
                )
        except (OSError, zipfile.BadZipFile, ValueError):
            findings.append(Finding("UNSCANNABLE_ARCHIVE", display, 1, _digest(display)))
        return findings
    if archive_kind in {"tar", "gzip", "xz", "bzip2", "zstd"} or _is_archive_name(
        path.name
    ):
        try:
            with tarfile.open(path, mode="r:*") as archive:
                findings.extend(_scan_tar(archive, display, depth=depth))
                tar_end = archive.offset
            if archive_kind in {"gzip", "xz", "bzip2"}:
                with path.open("rb") as raw_stream:
                    with _decompression_reader(raw_stream, archive_kind) as stream:
                        findings.extend(
                            _scan_stream(
                                f"{display}!decompressed-stream",
                                stream,
                                MAX_TEXT_BYTES + 1,
                                nonzero_tail_after=tar_end,
                            )
                        )
            elif _path_has_nonzero_bytes_after(path, tar_end):
                findings.append(
                    Finding(
                        "ARCHIVE_TRAILING_DATA",
                        display,
                        1,
                        _digest(display),
                    )
                )
            return findings
        except (
            OSError,
            EOFError,
            lzma.LZMAError,
            tarfile.TarError,
            ValueError,
        ):
            if (
                archive_kind in {"gzip", "xz", "bzip2"}
                and path.stat().st_size <= MAX_ZIP_MEMBER_BYTES
            ):
                findings.extend(
                    _scan_nested_bytes(path.read_bytes(), display, depth=depth)
                )
            else:
                findings.append(
                    Finding("UNSCANNABLE_ARCHIVE", display, 1, _digest(display))
                )
            return findings
    try:
        with path.open("rb") as stream:
            findings.extend(_scan_stream(display, stream, path.stat().st_size))
    except OSError:
        findings.append(Finding("UNSCANNABLE_FILE", display, 1, _digest(display)))
    return findings


def _git_paths(arguments: list[str]) -> list[Path]:
    proc = subprocess.run(
        ["git", *arguments], cwd=ROOT, check=True, capture_output=True
    )
    return [ROOT / os.fsdecode(raw) for raw in proc.stdout.split(b"\0") if raw]


def tracked_paths() -> list[Path]:
    return _git_paths(["ls-files", "-z"])


def diff_paths(base: str) -> list[Path]:
    subprocess.run(
        ["git", "cat-file", "-e", f"{base}^{{commit}}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    paths = _git_paths(["diff", "--name-only", "-z", "--diff-filter=ACMR", base, "--"])
    paths.extend(_git_paths(["ls-files", "--others", "--exclude-standard", "-z"]))
    return sorted({path for path in paths if path.exists()})


def scan_images(images: Iterable[str]) -> list[Finding]:
    findings: list[Finding] = []
    for image in images:
        subprocess.run(
            ["docker", "image", "inspect", image], check=True, capture_output=True
        )
        with tempfile.TemporaryDirectory(prefix="jiuye-secret-image-") as raw_dir:
            archive = Path(raw_dir) / "image.tar"
            subprocess.run(
                ["docker", "save", "-o", str(archive), image],
                check=True,
                capture_output=True,
            )
            findings.extend(scan_path(archive, display_path=f"image:{image}"))
    return findings


def _tar_bytes(members: dict[str, bytes]) -> bytes:
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode="w") as archive:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return target.getvalue()


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return target.getvalue()


def self_test() -> None:
    strong_one = base64.urlsafe_b64encode(
        hashlib.sha256(b"scanner-self-test-one").digest()
    ).decode().rstrip("=")
    strong_two = base64.urlsafe_b64encode(
        hashlib.sha256(b"scanner-self-test-two").digest()
    ).decode().rstrip("=")
    private_label = "PRIVATE KEY"
    private_body = base64.b64encode(
        hashlib.sha512(b"scanner-private-key-canary").digest()
    ).decode()
    private_key = (
        "-" * 5
        + "BEGIN "
        + private_label
        + "-" * 5
        + "\n"
        + private_body
        + "\n"
        + "-" * 5
        + "END "
        + private_label
        + "-" * 5
    )
    provider = "sk-" + "A7b_" * 10
    jwt_value = ".".join(
        base64.urlsafe_b64encode(part).decode().rstrip("=")
        for part in (
            b'{"alg":"HS256"}',
            b'{"sub":"canary","iat":1,"exp":4102444800}',
            b"signature-canary",
        )
    )
    bearer = "Bearer " + "Qx7_" * 10
    database_url = "postgresql://canary:" + strong_one + "@localhost/canary"
    assignment = "API_KEY=\"" + strong_two + "\""
    frontend = "VITE_PRIVATE_TOKEN=" + "V6n_" * 10
    historical = "admin" + str(100 + 23)
    samples = {
        "PRIVATE_KEY": private_key,
        "PROVIDER_TOKEN": provider,
        "JWT": jwt_value,
        "BEARER_TOKEN": bearer,
        "CREDENTIAL_URL": database_url,
        "HIGH_ENTROPY_ASSIGNMENT": assignment,
        "FRONTEND_SECRET": frontend,
        "HISTORICAL_DEMO_SECRET": historical,
    }
    observed: set[str] = set()
    findings: list[Finding] = []
    for expected, sample in samples.items():
        result = _scan_bytes(f"self-test/{expected}.txt", sample.encode())
        observed.update(finding.rule for finding in result)
        findings.extend(result)
    for path in (".env", "credentials.json", ".playwright-cli/session/Cookies"):
        result = _scan_bytes(f"self-test/{path}", b"")
        observed.update(finding.rule for finding in result)
        findings.extend(result)
    layer_one = _tar_bytes({"app/old-secret.txt": provider.encode()})
    layer_two = _tar_bytes({"app/.wh.old-secret.txt": b""})
    docker_save = _tar_bytes(
        {
            "blobs/sha256/layer-one": layer_one,
            "blobs/sha256/layer-two": layer_two,
            "manifest.json": json.dumps(
                [
                    {
                        "Layers": [
                            "blobs/sha256/layer-one",
                            "blobs/sha256/layer-two",
                        ]
                    }
                ]
            ).encode(),
        }
    )
    with tarfile.open(fileobj=io.BytesIO(docker_save), mode="r:") as archive:
        layer_findings = _scan_tar(archive, "self-test/deleted-layer-image.tar", depth=1)
    findings.extend(layer_findings)
    observed.update(finding.rule for finding in layer_findings)
    opaque_nested_zip = _zip_bytes(
        {"opaque-member": _zip_bytes({"payload.txt": provider.encode()})}
    )
    nested_findings = _scan_zip_bytes(
        opaque_nested_zip, "self-test/opaque-nested.zip", depth=1
    )
    findings.extend(nested_findings)
    nested_archive_detected = any(
        finding.rule == "PROVIDER_TOKEN" for finding in nested_findings
    )
    with tempfile.TemporaryDirectory(prefix="jiuye-secret-large-self-test-") as temp_dir:
        large_binary = Path(temp_dir) / "opaque-large.bin"
        with large_binary.open("wb") as handle:
            handle.seek(MAX_TEXT_BYTES + 8192)
            handle.write(provider.encode())
        large_findings = scan_path(
            large_binary, display_path="self-test/opaque-large.bin"
        )
    findings.extend(large_findings)
    large_binary_detected = any(
        finding.rule == "PROVIDER_TOKEN" for finding in large_findings
    )
    expected = set(samples) | {"SECRET_PATH"}
    missing = expected - observed
    rendered = "\n".join(finding.render() for finding in findings)
    leaked = [sample for sample in samples.values() if sample in rendered]
    safe = _scan_bytes(
        "self-test/safe.env.example",
        b"JWT_SECRET=<replace-at-install>\nVITE_API_BASE=/api/v1\n",
    )
    if (
        missing
        or leaked
        or safe
        or not nested_archive_detected
        or not large_binary_detected
    ):
        raise RuntimeError(
            "scanner self-test failed: "
            f"missing={sorted(missing)} leaked={bool(leaked)} "
            f"safe_findings={len(safe)} nested={nested_archive_detected} "
            f"large_binary={large_binary_detected}"
        )


def _unique(findings: Iterable[Finding]) -> list[Finding]:
    return sorted(set(findings))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_secrets.py")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--tracked", action="store_true")
    parser.add_argument("--diff", metavar="BASE")
    parser.add_argument("--paths", nargs="+", type=Path)
    parser.add_argument("--archive", action="append", type=Path, default=[])
    parser.add_argument("--image", action="append", default=[])
    args = parser.parse_args(argv)
    if not any(
        (args.self_test, args.tracked, args.diff, args.paths, args.archive, args.image)
    ):
        parser.error("at least one scan mode is required")
    try:
        if args.self_test:
            self_test()
        findings: list[Finding] = []
        paths: list[Path] = []
        if args.tracked:
            paths.extend(tracked_paths())
        if args.diff:
            paths.extend(diff_paths(args.diff))
        if args.paths:
            paths.extend(args.paths)
        paths.extend(args.archive)
        for path in sorted({path.resolve() for path in paths if path.exists()}):
            findings.extend(scan_path(path))
        if args.image:
            findings.extend(scan_images(args.image))
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[secret-scan] 扫描器错误：{type(exc).__name__}", file=sys.stderr)
        return 2
    findings = _unique(findings)
    if findings:
        for finding in findings:
            print(finding.render(), file=sys.stderr)
        print(f"[secret-scan] 失败：{len(findings)} 个高置信命中。", file=sys.stderr)
        return 1
    print("[secret-scan] 通过：未发现高置信秘密或受禁止凭据路径。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
