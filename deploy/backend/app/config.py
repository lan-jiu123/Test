"""
统一配置管理（参考 cup-team-main 架构）
使用 pydantic-settings 从 .env 读取，类型安全 + 默认值齐全
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from pydantic import field_validator
except Exception:  # pydantic v1 兼容
    field_validator = None
    try:
        from pydantic import validator as field_validator  # type: ignore
    except Exception:
        pass

try:
    from pydantic_settings import BaseSettings
except Exception:  # 没有 pydantic-settings 时，回退到 BaseModel + 手动读
    try:
        from pydantic import BaseModel as BaseSettings  # type: ignore
    except Exception:
        BaseSettings = object  # type: ignore

try:
    import json as _json
except Exception:
    _json = None


def _find_env_file() -> Optional[str]:
    """兼容多种场景查找 .env"""
    here = Path(__file__).resolve()               # backend/app/config.py
    candidates = [
        here.parent.parent.parent / ".env",      # project 根
        here.parent.parent / ".env",             # backend
        here.parent.parent.parent / "deploy" / ".env",  # deploy 目录
    ]
    for p in candidates:
        if p.is_file():
            return str(p)
    return None


_ENV_FILE = _find_env_file()


def _parse_json_list(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            if _json:
                return _json.loads(raw)
        except Exception:
            # 兼容逗号分隔
            return [s.strip() for s in raw.split(",") if s.strip()]
    return ["http://localhost:8000", "http://localhost:3000"]


class Settings(BaseSettings):
    # 运行环境
    ENVIRONMENT: str = "production"
    DEBUG: bool = False

    # API 服务
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    # 安全
    SECRET_KEY: str = "change-this-to-a-secure-random-string"
    ACCESS_TOKEN_EXPIRE_HOURS: int = 2

    # CORS
    CORS_ORIGINS: Any = '["http://localhost:8000","http://localhost:3000"]'

    # LLM 通用
    LLM_BACKEND: str = "longcat"  # longcat / ollama
    LLM_TEMPERATURE: float = 0.3
    LLM_TIMEOUT: int = 180

    # LongCat 云端
    LONGCAT_API_KEY: str = ""
    LONGCAT_API_URL: str = "https://api.longcat.chat/openai"
    LONGCAT_MODEL: str = "LongCat-2.0"

    # Ollama 本地
    OLLAMA_API_URL: str = "http://localhost:11434/v1"
    OLLAMA_MODEL: str = "qwen2.5:7b"

    # ---- 计算属性 ----
    @property
    def cors_origin_list(self) -> List[str]:
        return _parse_json_list(self.CORS_ORIGINS)

    @property
    def machine_arch(self) -> str:
        return os.uname().machine if hasattr(os, "uname") else "unknown"

    @property
    def is_loongarch(self) -> bool:
        return self.machine_arch.lower().startswith("loongarch")

    if field_validator:
        @field_validator("LONGCAT_API_URL", "OLLAMA_API_URL")
        @classmethod
        def _norm_url(cls, v: str) -> str:
            v = (v or "").strip()
            return v.rstrip("/") if v else v

    model_config = {
        "env_file": _ENV_FILE or ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    } if BaseSettings is not object else {}


# 兼容没有 pydantic-settings 的最小降级
def _fallback_settings() -> Settings:
    from dotenv import load_dotenv
    if _ENV_FILE:
        load_dotenv(_ENV_FILE, override=False)
    s = Settings()
    for fld in [
        "ENVIRONMENT", "DEBUG", "API_HOST", "API_PORT",
        "SECRET_KEY", "ACCESS_TOKEN_EXPIRE_HOURS", "CORS_ORIGINS",
        "LLM_BACKEND", "LLM_TEMPERATURE", "LLM_TIMEOUT",
        "LONGCAT_API_KEY", "LONGCAT_API_URL", "LONGCAT_MODEL",
        "OLLAMA_API_URL", "OLLAMA_MODEL",
    ]:
        env_v = os.getenv(fld)
        if env_v is None:
            continue
        try:
            import ast
            cur_type = type(getattr(s, fld, ""))
            if cur_type is bool:
                setattr(s, fld, env_v.lower() in ("1", "true", "yes", "on"))
            elif cur_type is int:
                setattr(s, fld, int(env_v))
            elif cur_type is float:
                setattr(s, fld, float(env_v))
            else:
                setattr(s, fld, env_v)
        except Exception:
            setattr(s, fld, env_v)
    return s


try:
    settings: Settings = Settings()
except Exception:
    settings = _fallback_settings()
