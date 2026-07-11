"""SQLite 数据库初始化和连接管理。"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("DATA_DIR", str(PROJECT_ROOT / "data"))).resolve()
DATABASE_PATH = Path(
    os.getenv("DATABASE_PATH", str(DATA_DIR / "equipment_maintenance.db"))
).resolve()
UPLOAD_DIR = Path(
    os.getenv("DOCUMENT_UPLOAD_DIR", str(DATA_DIR / "uploads" / "documents"))
).resolve()


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    connection = _connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL UNIQUE,
                file_type TEXT NOT NULL DEFAULT 'pdf',
                mime_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
                sha256 TEXT NOT NULL UNIQUE,
                page_count INTEGER,
                device_type TEXT,
                device_model TEXT,
                document_category TEXT,
                maintenance_level TEXT,
                status TEXT NOT NULL DEFAULT 'uploaded'
                    CHECK (status IN ('uploaded', 'parsing', 'indexing', 'ready', 'failed')),
                parse_error TEXT,
                uploaded_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_documents_status
                ON documents(status);
            CREATE INDEX IF NOT EXISTS idx_documents_device_model
                ON documents(device_model);
            CREATE INDEX IF NOT EXISTS idx_documents_created_at
                ON documents(created_at DESC);

            CREATE TABLE IF NOT EXISTS document_pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT NOT NULL,
                page_number INTEGER NOT NULL CHECK (page_number > 0),
                content TEXT NOT NULL,
                char_count INTEGER NOT NULL DEFAULT 0,
                is_toc INTEGER NOT NULL DEFAULT 0 CHECK (is_toc IN (0, 1)),
                created_at TEXT NOT NULL,
                FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
                UNIQUE (document_id, page_number)
            );

            CREATE INDEX IF NOT EXISTS idx_document_pages_document
                ON document_pages(document_id, page_number);

            CREATE TABLE IF NOT EXISTS document_chunks (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
                content TEXT NOT NULL,
                section_title TEXT,
                page_start INTEGER NOT NULL CHECK (page_start > 0),
                page_end INTEGER NOT NULL CHECK (page_end >= page_start),
                char_count INTEGER NOT NULL DEFAULT 0,
                device_type TEXT,
                device_model TEXT,
                component TEXT,
                safety_tags TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
                UNIQUE (document_id, chunk_index)
            );

            CREATE INDEX IF NOT EXISTS idx_document_chunks_document
                ON document_chunks(document_id, chunk_index);
            CREATE INDEX IF NOT EXISTS idx_document_chunks_pages
                ON document_chunks(document_id, page_start, page_end);
            CREATE INDEX IF NOT EXISTS idx_document_chunks_section
                ON document_chunks(section_title);

            CREATE TABLE IF NOT EXISTS chunk_embeddings (
                chunk_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                embedding_model TEXT NOT NULL,
                dimension INTEGER NOT NULL CHECK (dimension > 0),
                vector BLOB NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (chunk_id, embedding_model),
                FOREIGN KEY (chunk_id) REFERENCES document_chunks(id) ON DELETE CASCADE,
                FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_document
                ON chunk_embeddings(document_id, embedding_model);
            """
        )


def database_health() -> dict:
    try:
        with get_connection() as connection:
            connection.execute("SELECT 1").fetchone()
        return {"ok": True, "path": str(DATABASE_PATH)}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}
