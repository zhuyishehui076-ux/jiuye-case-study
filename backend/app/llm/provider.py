"""LLM provider 抽象层与三实现（SPEC §5）。

- OllamaProvider：P-LOCAL 默认，OpenAI 无关的 Ollama 原生 /api/chat + /api/embeddings。
- ApiProvider：仅 demo/cloud，OpenAI 兼容 /chat/completions；其他组合构造即抛异常。
- CachedProvider：装饰器，命中预计算/缓存直接返回（P-DEMO 演示确定性手段）。

complete_json：JSON 模式调用 → 解析 → jsonschema 校验；失败自动重试，最终失败抛 LLMParseError。
"""

from __future__ import annotations

import abc
import hashlib
import json
import re

import httpx
from jsonschema import ValidationError, validate

from app.core.config import settings

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)
_TIMEOUT = httpx.Timeout(600.0, connect=10.0)  # 本地 CPU 推理慢，给足超时


class LLMError(RuntimeError):
    pass


class LLMParseError(LLMError):
    """complete_json 多次重试后仍校验失败（SPEC §5）。"""


def _extract_json(text: str) -> dict:
    """从模型输出中提取 JSON 对象（容忍 ```json 围栏或前后噪声）。"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _JSON_FENCE.search(text)
    if m:
        return json.loads(m.group(1))
    # 退化：截取第一个 { 到最后一个 }
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise json.JSONDecodeError("未找到 JSON 对象", text, 0)


class Provider(abc.ABC):
    name: str = "provider"

    @abc.abstractmethod
    def _chat(self, system: str, user: str) -> str:
        """返回模型原始文本输出（期望为 JSON）。"""

    @abc.abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def complete_json(self, schema: dict, system: str, user: str, retries: int = 2) -> dict:
        last_err: Exception | None = None
        cur_user = user
        for _attempt in range(retries + 1):
            raw = self._chat(system, cur_user)
            try:
                data = _extract_json(raw)
                validate(instance=data, schema=schema)
                return data
            except (json.JSONDecodeError, ValidationError) as e:
                last_err = e
                # 重试时把错误回注，要求修正
                cur_user = (
                    f"{user}\n\n上次输出无法通过校验（{type(e).__name__}: {e}）。"
                    "请严格按 schema 只输出合法 JSON。"
                )
        raise LLMParseError(
            f"complete_json 重试 {retries} 次后仍失败：{type(last_err).__name__}: {last_err}"
        )


class OllamaProvider(Provider):
    """P-LOCAL 默认（Ollama 原生协议）。"""

    name = "ollama"

    def __init__(self) -> None:
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.ollama_model

    def _chat(self, system: str, user: str) -> str:
        options: dict[str, int | float] = {"temperature": 0}
        if settings.ollama_force_cpu:
            options["num_gpu"] = 0
        resp = httpx.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "format": "json",
                "stream": False,
                "options": options,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            payload: dict = {"model": self.model, "prompt": t}
            if settings.ollama_force_cpu:
                payload["options"] = {"num_gpu": 0}
            resp = httpx.post(
                f"{self.base_url}/api/embeddings",
                json=payload,
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            out.append(resp.json()["embedding"])
        return out


class ApiProvider(Provider):
    """P-DEMO 与开发期：OpenAI 兼容协议外部端点。

    档位守卫：仅 settings.allow_external_llm 为真时可构造。
    """

    name = "api"

    def __init__(self) -> None:
        if not settings.allow_external_llm:
            raise LLMError(
                "档位守卫触发：当前数据档位/部署拓扑禁止实例化 ApiProvider。"
                "production 或 local 请使用 OllamaProvider。"
            )
        self.api_key = settings.llm_api_key
        self.base_url = settings.llm_base_url.rstrip("/")
        self.model = settings.llm_model

    def _chat(self, system: str, user: str) -> str:
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = httpx.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": texts},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return [d["embedding"] for d in resp.json()["data"]]


def _cache_key(schema: dict, system: str, user: str) -> str:
    blob = json.dumps([schema, system, user], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


class CachedProvider(Provider):
    """装饰器：命中缓存直接返回（P-DEMO 演示确定性）。缺省用内存缓存，可注入自定义 store。"""

    name = "cached"

    def __init__(self, inner: Provider, store: dict[str, dict] | None = None) -> None:
        self.inner = inner
        self.store: dict[str, dict] = store if store is not None else {}

    def _chat(self, system: str, user: str) -> str:  # 不走文本层，complete_json 覆盖
        return self.inner._chat(system, user)

    def complete_json(self, schema: dict, system: str, user: str, retries: int = 2) -> dict:
        key = _cache_key(schema, system, user)
        if key in self.store:
            return self.store[key]
        result = self.inner.complete_json(schema, system, user, retries)
        self.store[key] = result
        return result

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.inner.embed(texts)


def get_provider() -> Provider:
    """按档位返回 provider —— 业务代码经此获取，运行时不可越权切换。"""
    return ApiProvider() if settings.allow_external_llm else OllamaProvider()
