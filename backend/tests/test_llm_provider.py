"""LLM provider 单测（SPEC §5）：schema 校验/重试、缓存、档位闸。LLM 输出用脚本化固定样本。"""

from __future__ import annotations

import os

import pytest

from app.llm.provider import (
    CachedProvider,
    LLMParseError,
    Provider,
    _extract_json,
)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["x"],
    "properties": {"x": {"type": "integer"}},
}


class ScriptProvider(Provider):
    """按预设脚本逐次返回 _chat 文本，用于验证 complete_json 的校验/重试。"""

    name = "script"

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls = 0

    def _chat(self, system: str, user: str) -> str:  # noqa: ARG002
        out = self.outputs[self.calls]
        self.calls += 1
        return out

    def embed(self, texts):  # noqa: ARG002
        return []


def test_complete_json_success_first_try():
    p = ScriptProvider(['{"x": 1}'])
    assert p.complete_json(SCHEMA, "s", "u") == {"x": 1}
    assert p.calls == 1


def test_complete_json_retries_on_bad_json_then_succeeds():
    p = ScriptProvider(["这不是json", '{"x": 2}'])
    assert p.complete_json(SCHEMA, "s", "u", retries=2) == {"x": 2}
    assert p.calls == 2


def test_complete_json_retries_on_schema_violation():
    p = ScriptProvider(['{"x": "非整数"}', '{"x": 3}'])
    assert p.complete_json(SCHEMA, "s", "u", retries=2) == {"x": 3}


def test_complete_json_raises_after_exhausting_retries():
    p = ScriptProvider(["bad", "still bad", "nope"])
    with pytest.raises(LLMParseError):
        p.complete_json(SCHEMA, "s", "u", retries=2)
    assert p.calls == 3  # 初次 + 2 次重试


def test_extract_json_handles_code_fence():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('前言 {"a": 2} 后语') == {"a": 2}


class CountingProvider(Provider):
    name = "counting"

    def __init__(self) -> None:
        self.n = 0

    def _chat(self, system, user):  # noqa: ARG002
        raise NotImplementedError

    def complete_json(self, schema, system, user, retries=2):  # noqa: ARG002
        self.n += 1
        return {"v": self.n}

    def embed(self, texts):  # noqa: ARG002
        return []


def test_cached_provider_serves_from_cache():
    inner = CountingProvider()
    cached = CachedProvider(inner)
    r1 = cached.complete_json(SCHEMA, "sys", "user")
    r2 = cached.complete_json(SCHEMA, "sys", "user")
    assert r1 == r2 == {"v": 1}
    assert inner.n == 1  # 第二次命中缓存，未调用 inner
    # 不同输入不命中
    cached.complete_json(SCHEMA, "sys", "other")
    assert inner.n == 2


def test_api_provider_blocked_in_local(monkeypatch):
    import app.core.config as config_mod
    import app.llm.provider as provider_mod
    from app.core.config import Settings

    s = Settings(
        _env_file=None,
        APP_PROFILE="local",
        DEPLOYMENT_MODE="local",
        DATABASE_URL=os.environ["DATABASE_URL"],
        JWT_SECRET=os.environ["JWT_SECRET"],
        FIELD_ENCRYPTION_KEY=os.environ["FIELD_ENCRYPTION_KEY"],
        INSTALLATION_ID=os.environ["INSTALLATION_ID"],
        LLM_API_KEY="",
        LLM_BASE_URL="",
        LLM_MODEL="",
    )
    monkeypatch.setattr(config_mod, "settings", s)
    monkeypatch.setattr(provider_mod, "settings", s)
    from app.llm.provider import ApiProvider, LLMError

    with pytest.raises(LLMError):
        ApiProvider()
