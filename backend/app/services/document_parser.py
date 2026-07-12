"""PDF 按页解析、章节识别和知识分块。"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

from ..database import get_connection


HEADER_RE = re.compile(r"^摩托车发动机维修手册\s*$")
FOOTER_RE = re.compile(r"^No\.\s*\d+\s*/\s*\d+\s*$", re.IGNORECASE)
MAJOR_SECTION_RE = re.compile(r"^[一二三四五六七八九十]+、\s*\S+")
NUMBERED_SECTION_RE = re.compile(r"^\d+(?:\.\d+)+\s+\S+")
ACTION_SECTION_RE = re.compile(r"^(拆卸|安装|检查|测量|调整)[^，。；：、,]{1,16}$")
MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+\S+")
SAFETY_RE = re.compile(r"(警告|警示|危险|严禁|不得|注意)")
WHITESPACE_RE = re.compile(r"[ \t\u3000]+")


@dataclass
class ParsedPage:
    page_number: int
    content: str
    is_toc: bool


@dataclass
class ChunkDraft:
    content: str
    section_title: str | None
    page_start: int
    page_end: int


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_page_text(raw_text: str) -> str:
    lines: list[str] = []
    blank_pending = False
    for raw_line in (raw_text or "").replace("\r", "\n").split("\n"):
        line = WHITESPACE_RE.sub(" ", raw_line).strip()
        if HEADER_RE.match(line) or FOOTER_RE.match(line):
            continue
        if not line:
            blank_pending = bool(lines)
            continue
        if blank_pending and lines and lines[-1] != "":
            lines.append("")
        lines.append(line)
        blank_pending = False
    return "\n".join(lines).strip()


def _is_section_heading(line: str) -> bool:
    value = line.strip()
    if len(value) > 40:
        return False
    return bool(
        MAJOR_SECTION_RE.match(value)
        or NUMBERED_SECTION_RE.match(value)
        or ACTION_SECTION_RE.match(value)
        or MARKDOWN_HEADING_RE.match(value)
        or value == "注意事项"
    )


def _is_table_of_contents(text: str, page_number: int) -> bool:
    if page_number > 5:
        return False
    heading_count = sum(
        1 for line in text.splitlines() if _is_section_heading(line)
    )
    return heading_count >= 8


def extract_pdf_pages(pdf_path: Path) -> list[ParsedPage]:
    reader = PdfReader(str(pdf_path), strict=False)
    if reader.is_encrypted:
        raise ValueError("暂不支持解析加密 PDF")

    pages: list[ParsedPage] = []
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            raw_text = page.extract_text(extraction_mode="layout") or ""
        except TypeError:  # 兼容较旧的 pypdf
            raw_text = page.extract_text() or ""
        content = _clean_page_text(raw_text)
        pages.append(
            ParsedPage(
                page_number=page_number,
                content=content,
                is_toc=_is_table_of_contents(content, page_number),
            )
        )
    return pages


def extract_text_pages(text_path: Path) -> list[ParsedPage]:
    try:
        content = text_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("文本文件必须使用 UTF-8 编码") from exc
    cleaned = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    return [ParsedPage(page_number=1, content=cleaned, is_toc=False)]


def extract_document_pages(path: Path) -> list[ParsedPage]:
    if path.suffix.lower() == ".pdf":
        return extract_pdf_pages(path)
    if path.suffix.lower() in {".md", ".markdown", ".txt"}:
        return extract_text_pages(path)
    raise ValueError("暂不支持该文档格式")


def _emit_section_chunks(
    units: list[tuple[str, int]],
    section_title: str | None,
    target_chars: int,
    overlap_chars: int,
) -> list[ChunkDraft]:
    if not units:
        return []

    chunks: list[ChunkDraft] = []
    cursor = 0
    while cursor < len(units):
        end = cursor
        char_count = 0
        while end < len(units):
            addition = len(units[end][0]) + (1 if char_count else 0)
            if end > cursor and char_count + addition > target_chars:
                break
            char_count += addition
            end += 1

        selected = units[cursor:end]
        content = "\n".join(text for text, _ in selected).strip()
        # 父标题后紧跟子标题时，不生成只有标题本身的无效小块。
        heading_only = len(selected) == 1 and _is_section_heading(selected[0][0])
        if content and not heading_only:
            pages = [page for _, page in selected]
            chunks.append(
                ChunkDraft(
                    content=content,
                    section_title=section_title,
                    page_start=min(pages),
                    page_end=max(pages),
                )
            )

        if end >= len(units):
            break

        overlap = 0
        next_cursor = end
        while next_cursor > cursor + 1 and overlap < overlap_chars:
            next_cursor -= 1
            overlap += len(units[next_cursor][0]) + 1
        cursor = max(next_cursor, cursor + 1)

    return chunks


def build_chunks(
    pages: list[ParsedPage], target_chars: int = 650, overlap_chars: int = 80
) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    current_section: str | None = None
    major_section: str | None = None
    numbered_section: str | None = None
    current_units: list[tuple[str, int]] = []

    def flush() -> None:
        nonlocal current_units
        chunks.extend(
            _emit_section_chunks(
                current_units, current_section, target_chars, overlap_chars
            )
        )
        current_units = []

    for page in pages:
        if page.is_toc or not page.content:
            continue
        for line in page.content.splitlines():
            value = line.strip()
            if not value:
                continue
            if _is_section_heading(value):
                flush()
                if MARKDOWN_HEADING_RE.match(value):
                    level = len(value) - len(value.lstrip("#"))
                    heading = value[level:].strip()
                    if level <= 2:
                        major_section = heading
                        numbered_section = None
                        current_section = heading
                    else:
                        parent = major_section
                        current_section = f"{parent} > {heading}" if parent else heading
                elif MAJOR_SECTION_RE.match(value):
                    major_section = value
                    numbered_section = None
                    current_section = value
                elif NUMBERED_SECTION_RE.match(value):
                    numbered_section = value
                    current_section = value
                elif ACTION_SECTION_RE.match(value) or value == "注意事项":
                    parent = numbered_section or major_section
                    current_section = f"{parent} > {value}" if parent else value
            current_units.append((value, page.page_number))
    flush()
    return chunks


def parse_document(
    document_id: str,
    pdf_path: Path,
    device_type: str | None = None,
    device_model: str | None = None,
) -> dict:
    now = _now_iso()
    with get_connection() as connection:
        connection.execute(
            "UPDATE documents SET status = 'parsing', parse_error = NULL, updated_at = ? WHERE id = ?",
            (now, document_id),
        )

    try:
        pages = extract_document_pages(pdf_path)
        chunks = build_chunks(pages)
        if not any(page.content for page in pages):
            raise ValueError("PDF 没有提取到文本，可能需要 OCR")
        if not chunks:
            raise ValueError("PDF 未生成有效知识块")

        now = _now_iso()
        with get_connection() as connection:
            connection.execute(
                "DELETE FROM document_chunks WHERE document_id = ?", (document_id,)
            )
            connection.execute(
                "DELETE FROM document_pages WHERE document_id = ?", (document_id,)
            )
            connection.executemany(
                """
                INSERT INTO document_pages (
                    document_id, page_number, content, char_count, is_toc, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        document_id,
                        page.page_number,
                        page.content,
                        len(page.content),
                        1 if page.is_toc else 0,
                        now,
                    )
                    for page in pages
                ],
            )
            connection.executemany(
                """
                INSERT INTO document_chunks (
                    id, document_id, chunk_index, content, section_title,
                    page_start, page_end, char_count, device_type, device_model,
                    safety_tags, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        uuid.uuid4().hex,
                        document_id,
                        index,
                        chunk.content,
                        chunk.section_title,
                        chunk.page_start,
                        chunk.page_end,
                        len(chunk.content),
                        device_type,
                        device_model,
                        "安全提醒" if SAFETY_RE.search(chunk.content) else None,
                        now,
                    )
                    for index, chunk in enumerate(chunks)
                ],
            )
            connection.execute(
                """
                UPDATE documents
                SET status = 'ready', parse_error = NULL, page_count = ?, updated_at = ?
                WHERE id = ?
                """,
                (len(pages), now, document_id),
            )
        return {
            "document_id": document_id,
            "status": "ready",
            "page_count": len(pages),
            "text_page_count": sum(bool(page.content) for page in pages),
            "toc_page_count": sum(page.is_toc for page in pages),
            "chunk_count": len(chunks),
        }
    except Exception as exc:
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE documents SET status = 'failed', parse_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (str(exc)[:1000], _now_iso(), document_id),
            )
        raise
