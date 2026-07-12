"""故障图片上传、视觉识别与 RAG 联合诊断接口。"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..config import settings
from ..services.llm_service import LLMConfigError, LLMQuotaError, LLMServiceError
from ..services.rag_service import answer_question
from ..services.vision_service import analyze_image, build_retrieval_query


router = APIRouter(prefix="/api/images", tags=["多模态图片诊断"])
ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAGIC_BYTES = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),
}


def _validate_magic(content: bytes, mime_type: str) -> bool:
    if mime_type == "image/webp":
        return content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    return any(content.startswith(prefix) for prefix in MAGIC_BYTES[mime_type])


@router.post("/diagnose")
async def diagnose_image(
    file: UploadFile = File(...),
    note: str = Form(default="", max_length=500),
    document_id: str | None = Form(default=None),
    device_model: str | None = Form(default=None),
    top_k: int = Form(default=5, ge=1, le=10),
):
    mime_type = (file.content_type or "").lower()
    if mime_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=415, detail="仅支持 JPG、PNG 或 WebP 图片")
    limit = int(settings.MAX_IMAGE_UPLOAD_MB or 10) * 1024 * 1024
    content = await file.read(limit + 1)
    await file.close()
    if not content:
        raise HTTPException(status_code=400, detail="上传图片为空")
    if len(content) > limit:
        raise HTTPException(status_code=413, detail=f"图片不能超过 {settings.MAX_IMAGE_UPLOAD_MB} MB")
    if not _validate_magic(content, mime_type):
        raise HTTPException(status_code=415, detail="文件内容与图片格式不符")

    try:
        analysis, vision_via = analyze_image(content, mime_type, note)
        query = build_retrieval_query(analysis, note)
        rag = answer_question(
            question=query or "识别图片中的设备部件并检索相关检修资料",
            document_id=document_id,
            device_model=device_model,
            top_k=top_k,
            min_lexical_coverage=0.30,
            min_matched_terms=2,
        )
        return {
            "filename": file.filename,
            "vision_analysis": analysis,
            "retrieval_query": query,
            "diagnosis": rag,
            "vision_via": vision_via,
        }
    except LLMQuotaError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    except LLMConfigError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except LLMServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="图片诊断服务暂时不可用，请检查模型配置和网络") from exc
