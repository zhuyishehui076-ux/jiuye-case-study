"""零出网守卫 + 档位守卫（DONE 判据：local 配外部密钥启动即拒）。"""

from __future__ import annotations

import os

import pytest

from app.core.config import ConfigError, Profile, Settings


def _make(**env) -> Settings:
    # Explicit test inputs win over the runner environment; no dotenv is read.
    explicit = {
        "DEPLOYMENT_MODE": "cloud",
        "DATABASE_URL": os.environ["DATABASE_URL"],
        "JWT_SECRET": os.environ["JWT_SECRET"],
        "FIELD_ENCRYPTION_KEY": os.environ["FIELD_ENCRYPTION_KEY"],
        "INSTALLATION_ID": os.environ["INSTALLATION_ID"],
        "LLM_API_KEY": "",
        "LLM_BASE_URL": "",
        "LLM_MODEL": "",
    }
    explicit.update(env)
    return Settings(_env_file=None, **explicit)


def test_local_with_external_key_refuses_to_start():
    with pytest.raises(ConfigError) as exc:
        _make(APP_PROFILE="local", LLM_API_KEY="sk-should-be-rejected")
    msg = str(exc.value)
    assert "零出网守卫" in msg
    assert "LLM_API_KEY" in msg  # 明确报错


def test_local_without_key_starts_fine():
    s = _make(APP_PROFILE="local", LLM_API_KEY="")
    assert s.profile is Profile.LOCAL
    assert s.allow_external_llm is False
    assert s.show_demo_badge is False
    assert s.network_isolated is True


def test_demo_with_external_key_allowed():
    s = _make(APP_PROFILE="demo", LLM_API_KEY="sk-demo-ok")
    assert s.profile is Profile.DEMO
    assert s.allow_external_llm is True
    assert s.show_demo_badge is True
    assert s.network_isolated is False


def test_api_provider_blocked_in_local(monkeypatch):
    """档位守卫：local 档实例化 ApiProvider 直接抛异常（SPEC §5）。"""
    import app.core.config as config_mod
    import app.llm.provider as provider_mod

    monkeypatch.setattr(config_mod, "settings", _make(APP_PROFILE="local"))
    monkeypatch.setattr(provider_mod, "settings", config_mod.settings)

    with pytest.raises(provider_mod.LLMError) as exc:
        provider_mod.ApiProvider()
    assert "档位守卫" in str(exc.value)


def test_get_provider_returns_ollama_in_local(monkeypatch):
    import app.core.config as config_mod
    import app.llm.provider as provider_mod

    monkeypatch.setattr(config_mod, "settings", _make(APP_PROFILE="local"))
    monkeypatch.setattr(provider_mod, "settings", config_mod.settings)

    assert isinstance(provider_mod.get_provider(), provider_mod.OllamaProvider)
