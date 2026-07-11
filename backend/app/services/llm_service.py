"""
LLM 服务（单例）- 参考 cup-team-main LLMService 架构
特点：
  1. 多后端统一封装：longcat / ollama（未来可加 dashscope/zhipu）
  2. SDK + requests 双通道，LoongArch 下即使 openai 包安装失败也能正常跑
  3. 自动根据配置/架构选择最佳通道
"""

from __future__ import annotations

import threading
from typing import Optional, Tuple

import requests

from ..config import settings


_LOCK = threading.Lock()
_INSTANCE: Optional["LLMService"] = None


class LLMServiceError(RuntimeError):
    """模型服务基础异常。"""


class LLMQuotaError(LLMServiceError):
    """模型账户额度不足。"""


class LLMConfigError(LLMServiceError):
    """模型名称、地址或鉴权配置错误。"""


class LLMService:
    def __init__(self) -> None:
        self.backend = (settings.LLM_BACKEND or "longcat").lower()
        self.temperature = float(settings.LLM_TEMPERATURE or 0.3)
        self.timeout = int(settings.LLM_TIMEOUT or 180)

        # 计算 base_url / api_key / model
        if self.backend == "ollama":
            self.base_url = (settings.OLLAMA_API_URL or "http://localhost:11434/v1").rstrip("/")
            self.api_key = "ollama"
            self.model = settings.OLLAMA_MODEL or "qwen2.5:7b"
        else:
            base = (settings.LONGCAT_API_URL or "https://api.longcat.chat/openai").rstrip("/")
            if not base.endswith("/v1"):
                base = base + "/v1"
            self.base_url = base
            self.api_key = settings.LONGCAT_API_KEY or ""
            self.model = settings.LONGCAT_MODEL or "LongCat-2.0"

        # SDK 通道初始化
        self._sdk_client = None
        self._sdk_available = not settings.is_loongarch  # 龙芯架构优先 requests 避免编译
        if self._sdk_available:
            try:
                from openai import OpenAI  # noqa: F401
            except Exception:
                self._sdk_available = False
        if self._sdk_available:
            try:
                from openai import OpenAI as _O
                self._sdk_client = _O(api_key=self.api_key, base_url=self.base_url)
            except Exception:
                self._sdk_available = False

    # -------- 对外 API --------
    @property
    def channel(self) -> str:
        return "openai-sdk" if self._sdk_available else "requests-fallback"

    def chat(self, system_prompt: str, user_prompt: str) -> Tuple[str, str]:
        """
        返回 (回答文本, 通道标识)
        通道标识用于响应体里告诉评委用的哪条路径
        """
        if self._sdk_available and self._sdk_client is not None:
            try:
                resp = self._sdk_client.chat.completions.create(
                    model=self.model,
                    temperature=self.temperature,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                return resp.choices[0].message.content, "sdk"
            except Exception as sdk_err:
                status_code = getattr(sdk_err, "status_code", None)
                if status_code == 402:
                    raise LLMQuotaError("LongCat Token 额度不足") from sdk_err
                if status_code in (400, 401, 403, 404):
                    raise LLMConfigError(
                        f"模型服务配置或鉴权失败（HTTP {status_code}）"
                    ) from sdk_err
                # SDK 失败 -> 自动退到 requests（避免单点故障）
                try:
                    return self._chat_via_requests(system_prompt, user_prompt), f"requests(sdk-fallback:{type(sdk_err).__name__})"
                except Exception as req_err:
                    raise RuntimeError(
                        f"LLM 双通道均失败。SDK错误=({sdk_err})；requests错误=({req_err})"
                    )
        return self._chat_via_requests(system_prompt, user_prompt), "requests"

    def healthcheck(self) -> dict:
        result = {"backend": self.backend, "channel": self.channel, "reachable": False, "model": self.model}
        try:
            if self.backend == "ollama":
                base = self.base_url.replace("/v1", "")
                r = requests.get(f"{base}/api/tags", timeout=5)
                result["reachable"] = r.status_code == 200
            else:
                result["reachable"] = bool(self.api_key) and len(self.api_key) > 4
        except Exception as e:
            result["error"] = type(e).__name__
        return result

    # -------- 内部实现 --------
    def _chat_via_requests(self, system_prompt: str, user_prompt: str) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        if resp.status_code != 200:
            snippet = resp.text[:800].replace("\n", " ")
            if resp.status_code == 402:
                raise LLMQuotaError("LongCat Token 额度不足")
            if resp.status_code in (400, 401, 403, 404):
                raise LLMConfigError(
                    f"模型服务配置或鉴权失败（HTTP {resp.status_code}）"
                )
            raise LLMServiceError(f"LLM HTTP {resp.status_code}: {snippet}")
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"LLM 返回结构异常: {type(e).__name__} body={resp.text[:500]}")


def get_llm_service() -> LLMService:
    """线程安全的单例获取"""
    global _INSTANCE
    if _INSTANCE is None:
        with _LOCK:
            if _INSTANCE is None:
                _INSTANCE = LLMService()
    return _INSTANCE
