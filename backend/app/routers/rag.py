"""带来源引用的 RAG 问答接口。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services.rag_service import answer_question
from ..services.llm_service import LLMConfigError, LLMQuotaError


router = APIRouter(prefix="/api/rag", tags=["可信 RAG 问答"])


class RAGRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    document_id: str | None = None
    device_model: str | None = None
    top_k: int = Field(default=5, ge=1, le=10)


@router.post("/ask")
def rag_ask(request: RAGRequest):
    try:
        return answer_question(
            question=request.question,
            document_id=request.document_id,
            device_model=request.device_model,
            top_k=request.top_k,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMQuotaError as exc:
        raise HTTPException(
            status_code=402,
            detail="LongCat Token 额度不足，请在开放平台补充额度后重试",
        ) from exc
    except LLMConfigError as exc:
        raise HTTPException(
            status_code=502,
            detail="模型配置或 API Key 无效，请检查 .env 配置",
        ) from exc
    except Exception as exc:
        # 不把上游响应、内部 URL 或鉴权信息直接暴露给浏览器。
        raise HTTPException(
            status_code=502,
            detail="模型服务暂时不可用，请检查模型名称、API Key、账户额度或网络连接",
        ) from exc
