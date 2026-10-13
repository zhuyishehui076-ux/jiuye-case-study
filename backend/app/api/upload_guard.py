"""用户文件上传的档位硬守卫（SPEC §13.2）。"""

from __future__ import annotations

from io import BytesIO
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from xml.etree.ElementTree import ParseError, fromstring
from xml.parsers import expat
from zipfile import BadZipFile, ZipFile

from fastapi import HTTPException, UploadFile, status

from app.core.config import settings

XLSX_MEDIA_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/zip",
}
MAX_XLSX_BYTES = 5 * 1024 * 1024
MAX_XLSX_ENTRIES = 200
MAX_XLSX_EXPANDED_BYTES = 50 * 1024 * 1024
MAX_XLSX_ENTRY_BYTES = 20 * 1024 * 1024
MAX_XLSX_COMPRESSION_RATIO = 100
MAX_XLSX_ROWS = 25_000
MAX_XLSX_CELLS = 400_000
MAX_XLSX_XML_ELEMENTS = 1_500_000
MAX_XLSX_TEXT_CHARS = 8 * 1024 * 1024
MAX_XLSX_SHEETS = 10
EXTERNAL_TARGET_SCHEMES = {"http", "https", "ftp", "file"}
SAFE_XLSX_EXACT_ENTRIES = {
    "[content_types].xml",
    "xl/workbook.xml",
    "xl/styles.xml",
    "xl/sharedstrings.xml",
    "xl/calcchain.xml",
    "xl/metadata.xml",
}
SAFE_XLSX_ENTRY_PREFIXES = (
    "_rels/",
    "docprops/",
    "xl/_rels/",
    "xl/theme/",
    "xl/worksheets/",
    "xl/tables/",
)


class _UnsafeWorkbook(ValueError):
    pass


def _is_safe_xlsx_entry(name: str) -> bool:
    lowered = name.lower().rstrip("/")
    return lowered in SAFE_XLSX_EXACT_ENTRIES or lowered.startswith(SAFE_XLSX_ENTRY_PREFIXES)


def _scan_xlsx_xml(workbook: ZipFile, names: list[str]) -> None:
    counts = {"rows": 0, "cells": 0, "elements": 0, "text": 0, "sheets": 0}

    def reject_forbidden_xml(*_args: object) -> None:
        raise _UnsafeWorkbook("forbidden XML declaration")

    for name in sorted(names):
        lowered = name.lower()
        if not (lowered.endswith(".xml") or lowered.endswith(".rels")):
            continue
        worksheet = lowered.startswith("xl/worksheets/") and lowered.endswith(".xml")
        parser = expat.ParserCreate(namespace_separator="}")
        parser.buffer_text = True

        def start_element(
            raw_name: str,
            _attributes: dict[str, str],
            is_worksheet: bool = worksheet,
            entry_name: str = lowered,
        ) -> None:
            local_name = raw_name.rsplit("}", 1)[-1].lower()
            counts["elements"] += 1
            if counts["elements"] > MAX_XLSX_XML_ELEMENTS:
                raise _UnsafeWorkbook("too many XML elements")
            if is_worksheet and local_name == "row":
                counts["rows"] += 1
                if counts["rows"] > MAX_XLSX_ROWS:
                    raise _UnsafeWorkbook("too many worksheet rows")
            elif is_worksheet and local_name == "c":
                counts["cells"] += 1
                if counts["cells"] > MAX_XLSX_CELLS:
                    raise _UnsafeWorkbook("too many worksheet cells")
            elif entry_name == "xl/workbook.xml" and local_name == "sheet":
                counts["sheets"] += 1
                if counts["sheets"] > MAX_XLSX_SHEETS:
                    raise _UnsafeWorkbook("too many worksheets")

        def character_data(value: str) -> None:
            counts["text"] += len(value)
            if counts["text"] > MAX_XLSX_TEXT_CHARS:
                raise _UnsafeWorkbook("too much workbook text")

        parser.StartElementHandler = start_element
        parser.CharacterDataHandler = character_data
        parser.StartDoctypeDeclHandler = reject_forbidden_xml
        parser.EntityDeclHandler = reject_forbidden_xml
        parser.ExternalEntityRefHandler = reject_forbidden_xml
        parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
        with workbook.open(name) as source:
            while chunk := source.read(64 * 1024):
                parser.Parse(chunk, False)
            parser.Parse(b"", True)


def require_user_file_uploads() -> None:
    """P-DEMO 永久拒绝用户文件，不能由前端显隐绕过。"""
    if not settings.allow_user_file_uploads:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="演示环境只允许使用系统内置的模拟样例，禁止上传用户文件。",
        )


async def read_validated_xlsx(
    file: UploadFile,
    *,
    max_bytes: int = MAX_XLSX_BYTES,
) -> bytes:
    """Read an XLSX with bounded memory and ZIP expansion checks."""
    filename = (file.filename or "").lower()
    if not filename.endswith(".xlsx"):
        raise HTTPException(status_code=415, detail="只支持 .xlsx 格式的 Excel 文件。")
    if file.content_type not in XLSX_MEDIA_TYPES:
        raise HTTPException(status_code=415, detail="文件类型不是受支持的 Excel 工作簿。")

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="文件过大，请控制在 5 MB 以内。")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data.startswith(b"PK\x03\x04"):
        raise HTTPException(status_code=400, detail="文件内容不是有效的 Excel 工作簿。")

    try:
        with ZipFile(BytesIO(data)) as workbook:
            entries = workbook.infolist()
            raw_names = [entry.filename for entry in entries]
            names = set(raw_names)
            expanded = sum(entry.file_size for entry in entries)
            compressed = max(sum(entry.compress_size for entry in entries), 1)
            if len(raw_names) != len(names):
                raise HTTPException(
                    status_code=400, detail="工作簿包含重复的内部文件，无法安全读取。"
                )
            for name in raw_names:
                normalized = name.replace("\\", "/")
                path = PurePosixPath(normalized)
                if normalized.startswith("/") or path.is_absolute() or ".." in path.parts:
                    raise HTTPException(
                        status_code=400, detail="工作簿内部路径异常，无法安全读取。"
                    )
                if not _is_safe_xlsx_entry(normalized):
                    raise HTTPException(status_code=400, detail="工作簿包含不受支持的内部内容。")
            if len(entries) > MAX_XLSX_ENTRIES:
                raise HTTPException(status_code=400, detail="工作簿内部文件过多，无法安全读取。")
            if expanded > MAX_XLSX_EXPANDED_BYTES:
                raise HTTPException(status_code=413, detail="工作簿展开后过大，无法安全读取。")
            if expanded / compressed > MAX_XLSX_COMPRESSION_RATIO:
                raise HTTPException(status_code=400, detail="工作簿压缩比例异常，无法安全读取。")
            if any(
                entry.file_size > MAX_XLSX_ENTRY_BYTES
                or entry.file_size / max(entry.compress_size, 1) > MAX_XLSX_COMPRESSION_RATIO
                for entry in entries
            ):
                raise HTTPException(
                    status_code=400, detail="工作簿内部文件大小或压缩比例异常，无法安全读取。"
                )
            if not {"[Content_Types].xml", "xl/workbook.xml"}.issubset(names):
                raise HTTPException(status_code=400, detail="工作簿结构不完整。")
            if any(name.lower().endswith("vbaproject.bin") for name in names):
                raise HTTPException(status_code=400, detail="不支持包含宏的工作簿。")
            if any(name.lower().startswith("xl/externallinks/") for name in names):
                raise HTTPException(status_code=400, detail="不支持包含外部数据连接的工作簿。")
            try:
                _scan_xlsx_xml(workbook, raw_names)
            except (_UnsafeWorkbook, expat.ExpatError) as exc:
                raise HTTPException(
                    status_code=400,
                    detail="工作簿结构或数据量异常，无法安全读取。",
                ) from exc
            relationship_names = [name for name in names if name.lower().endswith(".rels")]
            for name in relationship_names:
                payload = workbook.read(name)
                lowered = payload.lower()
                if b"<!doctype" in lowered or b"<!entity" in lowered:
                    raise HTTPException(
                        status_code=400,
                        detail="工作簿关系文件包含不安全的 XML 声明。",
                    )
                try:
                    root = fromstring(payload)
                except ParseError as exc:
                    raise HTTPException(
                        status_code=400,
                        detail="工作簿关系文件格式不正确。",
                    ) from exc
                for relationship in root.iter():
                    attributes = {
                        key.rsplit("}", 1)[-1].lower(): str(value).strip()
                        for key, value in relationship.attrib.items()
                    }
                    target_mode = attributes.get("targetmode", "").lower()
                    target = attributes.get("target", "")
                    if target_mode == "external" or urlsplit(target).scheme.lower() in (
                        EXTERNAL_TARGET_SCHEMES
                    ):
                        raise HTTPException(
                            status_code=400,
                            detail="不支持包含外部链接的工作簿。",
                        )
    except BadZipFile as exc:
        raise HTTPException(status_code=400, detail="文件内容不是有效的 Excel 工作簿。") from exc
    return data
