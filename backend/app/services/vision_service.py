"""轻量级多模态图片分析服务，兼容阿里云百炼 OpenAI 接口。"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

import requests

from ..config import settings
from .llm_service import LLMConfigError, LLMQuotaError, LLMServiceError


JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

# 图片模型输出不稳定时使用的确定性检修词表。长词优先，避免“轴承内圈”退化成“轴承”。
COMPONENT_TERMS = (
    "轴承内圈", "轴承外圈", "轴承套圈", "滚动体", "保持架", "轴承座",
    "密封环", "密封圈", "轴套", "滚道", "轴瓦", "轴承",
)
FAULT_TERMS = (
    "润滑失效", "润滑不足", "干摩擦", "粘着磨损", "磨粒磨损", "擦伤",
    "剥落", "点蚀", "凹坑", "磨损", "腐蚀", "锈蚀", "积碳", "结垢",
    "裂纹", "断裂", "压痕", "塑性变形", "电蚀", "过热", "污染",
)

VISION_SYSTEM_PROMPT = """你是设备检修图片分析助手。只描述图片中可以观察到的内容，
不得把推测写成事实。识别设备、部件、铭牌、型号、参数、告警码、裂纹、锈蚀、
漏油、烧蚀、磨损等现象。严格返回 JSON，不要使用 Markdown 代码块：
{
  "equipment": "设备类型，无法判断则为空字符串",
  "component": "部件名称，无法判断则为空字符串",
  "visible_facts": ["图片中直接可见的事实"],
  "ocr_text": ["可见文字、型号、参数或告警码"],
  "suspected_faults": ["疑似故障；必须使用疑似/可能措辞"],
  "search_keywords": ["用于维修知识库检索的部件名或标准故障术语，最多8个"],
  "confidence": 0.0,
  "needs_human_review": true,
  "review_reason": "需要人工复核的原因"
}
confidence 必须为 0 到 1 的数字。图片模糊、无设备或信息不足时降低置信度并说明。"""


def _parse_json(text: str) -> dict[str, Any]:
    match = JSON_RE.search(text or "")
    if not match:
        raise LLMServiceError("视觉模型未返回有效 JSON")
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise LLMServiceError("视觉模型返回的 JSON 无法解析") from exc
    if not isinstance(value, dict):
        raise LLMServiceError("视觉模型返回结构异常")
    return value


def _clean_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:20]


def analyze_image(image_bytes: bytes, mime_type: str, user_note: str = "") -> tuple[dict, str]:
    api_key = settings.QWEN_API_KEY or settings.LONGCAT_API_KEY
    base_url = (settings.QWEN_API_URL or settings.LONGCAT_API_URL).rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    model = settings.QWEN_VISION_MODEL
    if not api_key or not model:
        raise LLMConfigError("未配置 QWEN_API_KEY 或 QWEN_VISION_MODEL")

    image_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    note = user_note.strip() or "请分析这张设备检修现场图片。"
    payload = {
        "model": model,
        "temperature": 0.1,
        "stream": False,
        "messages": [
            {"role": "system", "content": VISION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": note},
                ],
            },
        ],
    }
    response = requests.post(
        f"{base_url}/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=int(settings.LLM_TIMEOUT or 180),
    )
    if response.status_code != 200:
        if response.status_code == 402:
            raise LLMQuotaError("视觉模型账户额度不足")
        if response.status_code in (400, 401, 403, 404):
            raise LLMConfigError(f"视觉模型配置或鉴权失败（HTTP {response.status_code}）")
        raise LLMServiceError(f"视觉模型服务异常（HTTP {response.status_code}）")
    try:
        raw = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise LLMServiceError("视觉模型响应结构异常") from exc

    result = _parse_json(raw)
    confidence = result.get("confidence", 0)
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.0
    normalized = {
        "equipment": str(result.get("equipment") or "").strip(),
        "component": str(result.get("component") or "").strip(),
        "visible_facts": _clean_list(result.get("visible_facts")),
        "ocr_text": _clean_list(result.get("ocr_text")),
        "suspected_faults": _clean_list(result.get("suspected_faults")),
        "search_keywords": _clean_list(result.get("search_keywords"))[:8],
        "confidence": confidence,
        "needs_human_review": bool(result.get("needs_human_review", True)),
        "review_reason": str(result.get("review_reason") or "图片诊断需由专业人员复核").strip(),
    }
    return normalized, "qwen-vision-requests"


def build_retrieval_query(analysis: dict, user_note: str = "") -> str:
    """构造短而专一的检索式，避免颜色、背景等视觉细节拉低词项覆盖率。"""
    source_text = "；".join(
        str(value)
        for value in (
            analysis.get("equipment", ""),
            analysis.get("component", ""),
            *analysis.get("visible_facts", []),
            *analysis.get("suspected_faults", []),
            *analysis.get("search_keywords", []),
        )
        if str(value).strip()
    )
    component_hits = [
        (source_text.find(term), -len(term), term)
        for term in COMPONENT_TERMS
        if term in source_text
    ]
    primary_component = min(component_hits)[2] if component_hits else ""
    fault_hits = sorted(
        (source_text.find(term), -len(term), term)
        for term in FAULT_TERMS
        if term in source_text
    )
    domain_term = "滚动轴承" if "轴承" in primary_component else ""
    extracted = ([domain_term] if domain_term else []) + ([primary_component] if primary_component else []) + [
        term for _, _, term in fault_hits[:5]
    ]
    # 只有规则词表完全没有命中时才使用模型关键词。模型自由生成的近义扩展
    # （如“铜合金迁移”“润滑脂碳化”）不得叠加到已有标准术语上污染覆盖率。
    model_keywords = [] if extracted else [
        str(value).strip()
        for value in analysis.get("search_keywords", [])
        if 1 < len(str(value).strip()) <= 12
    ][:5]
    equipment = str(analysis.get("equipment", "")).strip()
    if equipment in {"无法确定", "未知", "不确定", "无法识别"}:
        equipment = ""
    parts = [equipment, *extracted, *model_keywords]
    if not extracted:
        component = str(analysis.get("component", "")).strip()
        if component and len(component) <= 20:
            parts.insert(1, component)
    # OCR 只保留可能是型号、参数或告警码的短文本，避免整段铭牌污染检索。
    parts.extend(value for value in analysis.get("ocr_text", []) if len(value) <= 40)
    cleaned: list[str] = []
    seen: set[str] = set()
    for part in parts:
        value = str(part).strip().strip("；，。")
        if not value or value in seen:
            continue
        seen.add(value)
        cleaned.append(value)
    return "；".join(cleaned[:10])[:300]
