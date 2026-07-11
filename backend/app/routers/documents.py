"""知识库文档上传与管理接口。"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pypdf import PdfReader

from ..database import UPLOAD_DIR, get_connection, init_database
from ..services.document_parser import parse_document


router = APIRouter(prefix="/api/documents", tags=["知识库文档"])

MAX_UPLOAD_BYTES = int(os.getenv("MAX_DOCUMENT_UPLOAD_MB", "100")) * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"application/pdf", "application/octet-stream"}
READ_CHUNK_SIZE = 1024 * 1024


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_optional(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _row_to_document(row: sqlite3.Row) -> dict:
    document = dict(row)
    document["download_url"] = f"/api/documents/{document['id']}/file"
    return document


def _get_document_or_404(document_id: str) -> sqlite3.Row:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return row


def _validate_pdf(path: Path) -> int:
    try:
        with path.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise ValueError("文件头不是 PDF")
            stream.seek(0)
            reader = PdfReader(stream, strict=False)
            if reader.is_encrypted:
                raise ValueError("暂不支持加密 PDF")
            page_count = len(reader.pages)
            if page_count < 1:
                raise ValueError("PDF 没有可用页面")
            return page_count
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("PDF 文件损坏或无法解析") from exc


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(..., description="仅支持 PDF 文件"),
    title: str | None = Form(default=None),
    device_type: str | None = Form(default=None),
    device_model: str | None = Form(default=None),
    document_category: str | None = Form(default=None),
    maintenance_level: str | None = Form(default=None),
    uploaded_by: str | None = Form(default=None),
):
    """流式保存 PDF，校验文件并用 SHA-256 去重。"""
    init_database()
    original_filename = Path(file.filename or "").name
    if not original_filename or Path(original_filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=415, detail="仅支持 .pdf 文件")
    if file.content_type and file.content_type.lower() not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="文件 MIME 类型不是 PDF")

    document_id = uuid.uuid4().hex
    temp_path = UPLOAD_DIR / f".{document_id}.part"
    stored_filename = f"{document_id}.pdf"
    final_path = UPLOAD_DIR / stored_filename
    digest = hashlib.sha256()
    size_bytes = 0

    try:
        with temp_path.open("wb") as output:
            while chunk := await file.read(READ_CHUNK_SIZE):
                size_bytes += len(chunk)
                if size_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB 限制",
                    )
                digest.update(chunk)
                output.write(chunk)
    finally:
        await file.close()

    try:
        if size_bytes == 0:
            raise HTTPException(status_code=400, detail="上传文件为空")
        try:
            page_count = _validate_pdf(temp_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        sha256 = digest.hexdigest()
        with get_connection() as connection:
            duplicate = connection.execute(
                "SELECT id, title, original_filename FROM documents WHERE sha256 = ?",
                (sha256,),
            ).fetchone()
            if duplicate is not None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "相同内容的文档已存在",
                        "document_id": duplicate["id"],
                        "title": duplicate["title"],
                    },
                )

            now = _now_iso()
            connection.execute(
                """
                INSERT INTO documents (
                    id, title, original_filename, stored_filename, file_type,
                    mime_type, size_bytes, sha256, page_count, device_type,
                    device_model, document_category, maintenance_level, status,
                    uploaded_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pdf', ?, ?, ?, ?, ?, ?, ?, ?, 'uploaded', ?, ?, ?)
                """,
                (
                    document_id,
                    _clean_optional(title) or Path(original_filename).stem,
                    original_filename,
                    stored_filename,
                    file.content_type or "application/pdf",
                    size_bytes,
                    sha256,
                    page_count,
                    _clean_optional(device_type),
                    _clean_optional(device_model),
                    _clean_optional(document_category),
                    _clean_optional(maintenance_level),
                    _clean_optional(uploaded_by),
                    now,
                    now,
                ),
            )
            os.replace(temp_path, final_path)

        return _row_to_document(_get_document_or_404(document_id))
    except Exception:
        temp_path.unlink(missing_ok=True)
        # 数据库写入成功但移动文件失败时，清理孤立记录。
        if not final_path.exists():
            with get_connection() as connection:
                connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        raise


@router.get("")
def list_documents(
    document_status: str | None = Query(default=None, alias="status"),
    device_model: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    init_database()
    clauses: list[str] = []
    params: list[object] = []
    if document_status:
        clauses.append("status = ?")
        params.append(document_status)
    if device_model:
        clauses.append("device_model = ?")
        params.append(device_model)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

    with get_connection() as connection:
        total = connection.execute(
            f"SELECT COUNT(*) FROM documents{where}", params
        ).fetchone()[0]
        rows = connection.execute(
            f"SELECT * FROM documents{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return {"total": total, "items": [_row_to_document(row) for row in rows]}


@router.get("/{document_id}")
def get_document(document_id: str):
    init_database()
    return _row_to_document(_get_document_or_404(document_id))


@router.get("/{document_id}/status")
def get_document_status(document_id: str):
    init_database()
    row = _get_document_or_404(document_id)
    with get_connection() as connection:
        page_total = connection.execute(
            "SELECT COUNT(*) FROM document_pages WHERE document_id = ?", (document_id,)
        ).fetchone()[0]
        chunk_total = connection.execute(
            "SELECT COUNT(*) FROM document_chunks WHERE document_id = ?", (document_id,)
        ).fetchone()[0]
        embedding_row = connection.execute(
            """
            SELECT COUNT(*) AS total, embedding_model, dimension
            FROM chunk_embeddings WHERE document_id = ?
            GROUP BY embedding_model, dimension ORDER BY total DESC LIMIT 1
            """,
            (document_id,),
        ).fetchone()
    return {
        "id": row["id"],
        "status": row["status"],
        "parse_error": row["parse_error"],
        "page_count": row["page_count"],
        "parsed_page_count": page_total,
        "chunk_count": chunk_total,
        "indexed_chunk_count": embedding_row["total"] if embedding_row else 0,
        "embedding_model": embedding_row["embedding_model"] if embedding_row else None,
        "embedding_dimension": embedding_row["dimension"] if embedding_row else None,
        "updated_at": row["updated_at"],
    }


@router.post("/{document_id}/parse")
def parse_uploaded_document(document_id: str):
    """解析 PDF，保存逐页文本并生成带章节与页码的知识块。"""
    init_database()
    row = _get_document_or_404(document_id)
    path = UPLOAD_DIR / row["stored_filename"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文档文件不存在")
    try:
        return parse_document(
            document_id=document_id,
            pdf_path=path,
            device_type=row["device_type"],
            device_model=row["device_model"],
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"PDF 解析失败：{exc}") from exc


@router.post("/{document_id}/reparse")
def reparse_uploaded_document(document_id: str):
    return parse_uploaded_document(document_id)


@router.get("/{document_id}/pages")
def list_document_pages(
    document_id: str,
    page: int | None = Query(default=None, ge=1),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    init_database()
    _get_document_or_404(document_id)
    where = "document_id = ?"
    params: list[object] = [document_id]
    if page is not None:
        where += " AND page_number = ?"
        params.append(page)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT page_number, content, char_count, is_toc
            FROM document_pages WHERE {where}
            ORDER BY page_number LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@router.get("/{document_id}/chunks")
def list_document_chunks(
    document_id: str,
    page: int | None = Query(default=None, ge=1),
    section: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    init_database()
    _get_document_or_404(document_id)
    clauses = ["document_id = ?"]
    params: list[object] = [document_id]
    if page is not None:
        clauses.append("page_start <= ? AND page_end >= ?")
        params.extend([page, page])
    if section:
        clauses.append("section_title LIKE ?")
        params.append(f"%{section.strip()}%")
    where = " AND ".join(clauses)
    with get_connection() as connection:
        total = connection.execute(
            f"SELECT COUNT(*) FROM document_chunks WHERE {where}", params
        ).fetchone()[0]
        rows = connection.execute(
            f"""
            SELECT id, chunk_index, content, section_title, page_start, page_end,
                   char_count, device_type, device_model, component, safety_tags
            FROM document_chunks WHERE {where}
            ORDER BY chunk_index LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
    return {"total": total, "items": [dict(row) for row in rows]}


@router.get("/{document_id}/file", response_class=FileResponse)
def download_document(document_id: str):
    init_database()
    row = _get_document_or_404(document_id)
    path = UPLOAD_DIR / row["stored_filename"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文档文件不存在")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=row["original_filename"],
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: str):
    init_database()
    row = _get_document_or_404(document_id)
    path = UPLOAD_DIR / row["stored_filename"]
    with get_connection() as connection:
        connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    path.unlink(missing_ok=True)
    return None
