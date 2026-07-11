"""基于检索证据生成带可验证引用的 RAG 回答。"""

from __future__ import annotations

import json
import re
from typing import Any

from .llm_service import get_llm_service
from .retrieval_service import hybrid_search


CITATION_RE = re.compile(r"\[S(\d+)\]")
JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
MIN_LEXICAL_COVERAGE = 0.70


SYSTEM_PROMPT = """你是设备检修知识库助手。你只能依据用户消息中的【检索证据】回答。

严格规则：
1. 不得使用证据之外的型号、参数、步骤和结论。
2. 每个关键结论后必须标注证据编号，例如 [S1]。
3. 不得编造不存在的证据编号、文档、章节或页码。
4. 不同设备或型号的内容不得混用。
5. 证据不足时必须明确说明“现有知识库证据不足”。
6. 涉及拆装、旋转、高温或电气风险时给出安全提醒。
7. 返回严格 JSON，不要使用 Markdown 代码围栏。

JSON 格式：
{
  "summary": "简明结论，包含[Sx]引用",
  "possible_causes": ["原因及[Sx]引用"],
  "inspection_steps": ["步骤及[Sx]引用"],
  "safety_warnings": ["提醒及[Sx]引用"],
  "citation_ids": ["S1"]
}
"""


def _citation_from_result(item: dict, evidence_id: str) -> dict:
    page_label = (
        str(item["page_start"])
        if item["page_start"] == item["page_end"]
        else f"{item['page_start']}-{item['page_end']}"
    )
    return {
        "id": evidence_id,
        "document_id": item["document_id"],
        "document_title": item["document_title"],
        "section_title": item["section_title"],
        "page_start": item["page_start"],
        "page_end": item["page_end"],
        "page_label": page_label,
        "excerpt": item["content"][:500],
        "file_url": f"/api/documents/{item['document_id']}/file#page={item['page_start']}",
        "retrieval_rank": item["rank"],
    }


def _insufficient(search_result: dict) -> tuple[bool, str | None]:
    if not search_result["items"]:
        return True, "知识库中没有可用的检修资料"
    diagnostics = search_result.get("diagnostics") or {}
    if diagnostics.get("missing_codes"):
        return True, "问题中的型号或故障码未在知识库证据中出现"
    if diagnostics.get("lexical_coverage", 0.0) < MIN_LEXICAL_COVERAGE:
        return True, "检索证据与问题的关键术语匹配度不足"
    return False, None


def _parse_model_json(raw: str) -> dict[str, Any] | None:
    match = JSON_BLOCK_RE.search(raw or "")
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def _render_structured_answer(payload: dict[str, Any]) -> str:
    sections: list[str] = []
    summary = str(payload.get("summary") or "").strip()
    if summary:
        sections.append(f"【诊断结论】\n{summary}")
    for title, key in (
        ("可能原因", "possible_causes"),
        ("检查与处理步骤", "inspection_steps"),
        ("安全提醒", "safety_warnings"),
    ):
        values = payload.get(key) or []
        if isinstance(values, str):
            values = [values]
        cleaned = [str(value).strip() for value in values if str(value).strip()]
        if cleaned:
            sections.append(
                f"【{title}】\n" + "\n".join(
                    f"{index}. {value}" for index, value in enumerate(cleaned, start=1)
                )
            )
    return "\n\n".join(sections) or "现有知识库证据不足。"


def answer_question(
    question: str,
    document_id: str | None = None,
    device_model: str | None = None,
    top_k: int = 5,
    llm_service=None,
) -> dict:
    search_result = hybrid_search(
        question,
        document_id=document_id,
        device_model=device_model,
        top_k=top_k,
    )
    insufficient, reason = _insufficient(search_result)
    if insufficient:
        return {
            "answerable": False,
            "answer": f"现有知识库证据不足，无法可靠回答该问题。{reason or ''}",
            "citations": [],
            "retrieval": search_result["diagnostics"],
            "llm_via": "not_called",
        }

    evidence_items = search_result["items"]
    citations = [
        _citation_from_result(item, f"S{index}")
        for index, item in enumerate(evidence_items, start=1)
    ]
    evidence_text = "\n\n".join(
        f"[{citation['id']}] 文档：《{citation['document_title']}》；"
        f"章节：{citation['section_title'] or '未识别'}；PDF页码：{citation['page_label']}\n"
        f"{citation['excerpt']}"
        for citation in citations
    )
    user_prompt = f"用户问题：\n{question}\n\n【检索证据】\n{evidence_text}"
    service = llm_service or get_llm_service()
    raw_answer, llm_via = service.chat(SYSTEM_PROMPT, user_prompt)
    payload = _parse_model_json(raw_answer)
    answer = _render_structured_answer(payload) if payload else raw_answer.strip()

    valid_ids = {citation["id"] for citation in citations}
    referenced = {
        f"S{number}" for number in CITATION_RE.findall(answer) if f"S{number}" in valid_ids
    }
    if payload and isinstance(payload.get("citation_ids"), list):
        referenced.update(
            str(value) for value in payload["citation_ids"] if str(value) in valid_ids
        )
    # 模型未按要求标注时仍返回第一条真实证据，但明确标记未在正文引用。
    selected = [citation for citation in citations if citation["id"] in referenced]
    if not selected:
        selected = citations[:1]
    for citation in selected:
        citation["used_in_answer"] = citation["id"] in referenced

    return {
        "answerable": True,
        "answer": answer,
        "citations": selected,
        "retrieval": search_result["diagnostics"],
        "llm_via": llm_via,
    }
