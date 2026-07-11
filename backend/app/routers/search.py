"""知识库索引与混合检索接口。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..services.retrieval_service import hybrid_search, index_document


router = APIRouter(prefix="/api/search", tags=["知识检索"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    document_id: str | None = None
    device_model: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)


@router.post("/index/{document_id}")
def build_document_index(document_id: str):
    try:
        return index_document(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"建立索引失败：{exc}") from exc


@router.post("")
def search_knowledge(request: SearchRequest):
    try:
        return hybrid_search(
            query=request.query,
            document_id=request.document_id,
            device_model=request.device_model,
            top_k=request.top_k,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
def search_knowledge_get(
    query: str = Query(min_length=1, max_length=1000),
    document_id: str | None = None,
    device_model: str | None = None,
    top_k: int = Query(default=5, ge=1, le=20),
):
    try:
        return hybrid_search(query, document_id, device_model, top_k)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
