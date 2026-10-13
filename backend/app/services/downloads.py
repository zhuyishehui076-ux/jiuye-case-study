"""面向中文用户的下载文件名。"""

from __future__ import annotations

from urllib.parse import quote


def chinese_attachment(filename: str, *, fallback: str) -> str:
    """兼容旧浏览器的英文回退名，同时让现代浏览器显示中文文件名。"""

    return (
        f'attachment; filename="{fallback}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )
