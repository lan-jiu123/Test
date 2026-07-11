"""LoongArch 友好的 BM25、向量检索与 RRF 混合召回。"""

from __future__ import annotations

import hashlib
import math
import os
import re
from array import array
from collections import Counter
from datetime import datetime, timezone
from typing import Iterable

import requests

try:
    import jieba
except ImportError:  # 依赖未安装时仍保留字符级兜底
    jieba = None

from ..database import get_connection


TOKEN_RE = re.compile(r"[A-Za-z]+(?:[-_.][A-Za-z0-9]+)*|\d+(?:\.\d+)*|[\u4e00-\u9fff]")
DEFAULT_DIMENSION = int(os.getenv("LOCAL_EMBEDDING_DIMENSION", "384"))
EMBEDDING_BACKEND = os.getenv("EMBEDDING_BACKEND", "local_hash").lower()
EMBEDDING_API_URL = os.getenv("EMBEDDING_API_URL", "").rstrip("/")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "local-char-ngram-v1")
RRF_K = 60
QUERY_STOP_TERMS = {
    "什么", "如何", "怎么", "怎样", "多少", "是否", "能否", "这款", "分别",
    "对应", "应该", "需要", "进行", "可以", "方法", "问题", "使用",
}
CODE_RE = re.compile(r"(?i)(?:[A-Z]+)?\d{2,}[A-Z0-9._-]*")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tokenize(text: str) -> list[str]:
    """英文/数字词元 + 中文单字和双字词元，纯 Python 且无需分词模型。"""
    raw = [token.lower() for token in TOKEN_RE.findall(text or "")]
    tokens = list(raw)
    chinese = [token for token in raw if "\u4e00" <= token <= "\u9fff"]
    tokens.extend(chinese[i] + chinese[i + 1] for i in range(len(chinese) - 1))
    return tokens


def _salient_query_terms(text: str) -> set[str]:
    return _semantic_terms(text)


def _semantic_terms(text: str) -> set[str]:
    if jieba is not None:
        candidates = [value.strip().lower() for value in jieba.lcut(text or "")]
    else:
        candidates = tokenize(text)
    return {
        token
        for token in candidates
        if len(token) >= 2
        and token not in QUERY_STOP_TERMS
        and re.search(r"[A-Za-z0-9\u4e00-\u9fff]", token)
    }


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def _local_embedding(text: str, dimension: int = DEFAULT_DIMENSION) -> list[float]:
    """稳定的字符 n-gram 特征向量，作为无模型环境下的离线检索兜底。"""
    vector = [0.0] * dimension
    normalized = re.sub(r"\s+", "", (text or "").lower())
    features = tokenize(text)
    features.extend(normalized[i : i + 3] for i in range(max(0, len(normalized) - 2)))
    for feature in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        number = int.from_bytes(digest, "little")
        index = number % dimension
        sign = 1.0 if (number >> 63) == 0 else -1.0
        vector[index] += sign
    return _normalize(vector)


def _api_embeddings(texts: list[str]) -> list[list[float]]:
    if not EMBEDDING_API_URL or not EMBEDDING_API_KEY:
        raise RuntimeError("EMBEDDING_API_URL 或 EMBEDDING_API_KEY 未配置")
    response = requests.post(
        EMBEDDING_API_URL,
        headers={
            "Authorization": f"Bearer {EMBEDDING_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"model": EMBEDDING_MODEL, "input": texts},
        timeout=180,
    )
    response.raise_for_status()
    data = sorted(response.json()["data"], key=lambda item: item.get("index", 0))
    return [_normalize([float(value) for value in item["embedding"]]) for item in data]


def embed_texts(texts: list[str]) -> list[list[float]]:
    if EMBEDDING_BACKEND == "api":
        return _api_embeddings(texts)
    return [_local_embedding(text) for text in texts]


def _vector_to_blob(vector: list[float]) -> bytes:
    return array("f", vector).tobytes()


def _blob_to_vector(blob: bytes) -> array:
    values = array("f")
    values.frombytes(blob)
    return values


def _chunk_embedding_text(chunk: dict) -> str:
    parts = [
        chunk.get("section_title") or "",
        chunk.get("device_type") or "",
        chunk.get("device_model") or "",
        chunk.get("content") or "",
    ]
    return "\n".join(part for part in parts if part)


def index_document(document_id: str, batch_size: int = 32) -> dict:
    with get_connection() as connection:
        document = connection.execute(
            "SELECT id, status FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        if document is None:
            raise ValueError("文档不存在")
        chunks = [
            dict(row)
            for row in connection.execute(
                """
                SELECT id, content, section_title, device_type, device_model
                FROM document_chunks WHERE document_id = ? ORDER BY chunk_index
                """,
                (document_id,),
            ).fetchall()
        ]
        if not chunks:
            raise ValueError("文档尚未解析或没有知识块")
        connection.execute(
            "UPDATE documents SET status = 'indexing', updated_at = ? WHERE id = ?",
            (_now_iso(), document_id),
        )

    try:
        indexed: list[tuple] = []
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            vectors = embed_texts([_chunk_embedding_text(chunk) for chunk in batch])
            if len(vectors) != len(batch):
                raise RuntimeError("Embedding 返回数量与输入不一致")
            for chunk, vector in zip(batch, vectors):
                indexed.append(
                    (
                        chunk["id"],
                        document_id,
                        EMBEDDING_MODEL,
                        len(vector),
                        _vector_to_blob(vector),
                        _now_iso(),
                    )
                )

        with get_connection() as connection:
            connection.execute(
                "DELETE FROM chunk_embeddings WHERE document_id = ? AND embedding_model = ?",
                (document_id, EMBEDDING_MODEL),
            )
            connection.executemany(
                """
                INSERT INTO chunk_embeddings (
                    chunk_id, document_id, embedding_model, dimension, vector, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                indexed,
            )
            connection.execute(
                "UPDATE documents SET status = 'ready', parse_error = NULL, updated_at = ? WHERE id = ?",
                (_now_iso(), document_id),
            )
        return {
            "document_id": document_id,
            "status": "ready",
            "embedding_backend": EMBEDDING_BACKEND,
            "embedding_model": EMBEDDING_MODEL,
            "dimension": indexed[0][3],
            "indexed_chunk_count": len(indexed),
        }
    except Exception as exc:
        with get_connection() as connection:
            connection.execute(
                "UPDATE documents SET status = 'failed', parse_error = ?, updated_at = ? WHERE id = ?",
                (str(exc)[:1000], _now_iso(), document_id),
            )
        raise


def _bm25_scores(query_tokens: list[str], corpora: list[list[str]]) -> list[float]:
    if not corpora or not query_tokens:
        return [0.0] * len(corpora)
    k1, b = 1.5, 0.75
    lengths = [len(tokens) for tokens in corpora]
    average_length = sum(lengths) / max(len(lengths), 1)
    document_frequency: Counter[str] = Counter()
    term_frequencies: list[Counter[str]] = []
    for tokens in corpora:
        frequencies = Counter(tokens)
        term_frequencies.append(frequencies)
        document_frequency.update(frequencies.keys())

    total = len(corpora)
    scores = [0.0] * total
    for term in set(query_tokens):
        df = document_frequency.get(term, 0)
        if df == 0:
            continue
        idf = math.log(1.0 + (total - df + 0.5) / (df + 0.5))
        for index, frequencies in enumerate(term_frequencies):
            frequency = frequencies.get(term, 0)
            if frequency == 0:
                continue
            denominator = frequency + k1 * (
                1 - b + b * lengths[index] / max(average_length, 1)
            )
            scores[index] += idf * frequency * (k1 + 1) / denominator
    return scores


def _ranked_ids(scores: Iterable[tuple[str, float]], limit: int) -> list[tuple[str, float]]:
    return sorted(scores, key=lambda item: item[1], reverse=True)[:limit]


def hybrid_search(
    query: str,
    document_id: str | None = None,
    device_model: str | None = None,
    top_k: int = 5,
    candidate_k: int = 30,
) -> dict:
    cleaned_query = (query or "").strip()
    if not cleaned_query:
        raise ValueError("检索问题不能为空")

    clauses = ["d.status = 'ready'"]
    params: list[object] = []
    if document_id:
        clauses.append("c.document_id = ?")
        params.append(document_id)
    if device_model:
        clauses.append("(c.device_model = ? OR c.device_model IS NULL)")
        params.append(device_model)
    where = " AND ".join(clauses)

    with get_connection() as connection:
        chunks = [
            dict(row)
            for row in connection.execute(
                f"""
                SELECT c.*, d.title AS document_title
                FROM document_chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE {where}
                ORDER BY c.document_id, c.chunk_index
                """,
                params,
            ).fetchall()
        ]
        embedding_rows = connection.execute(
            f"""
            SELECT e.chunk_id, e.vector
            FROM chunk_embeddings e
            JOIN document_chunks c ON c.id = e.chunk_id
            JOIN documents d ON d.id = c.document_id
            WHERE {where} AND e.embedding_model = ?
            """,
            [*params, EMBEDDING_MODEL],
        ).fetchall()
    if not chunks:
        return {"query": cleaned_query, "total": 0, "items": []}

    chunk_by_id = {chunk["id"]: chunk for chunk in chunks}
    corpora = [
        tokenize(f"{chunk.get('section_title') or ''} {chunk['content']}")
        for chunk in chunks
    ]
    bm25_values = _bm25_scores(tokenize(cleaned_query), corpora)
    bm25_ranked = _ranked_ids(
        ((chunk["id"], score) for chunk, score in zip(chunks, bm25_values)),
        candidate_k,
    )

    query_vector = embed_texts([cleaned_query])[0]
    vector_scores: list[tuple[str, float]] = []
    for row in embedding_rows:
        vector = _blob_to_vector(row["vector"])
        if len(vector) != len(query_vector):
            continue
        score = sum(left * right for left, right in zip(query_vector, vector))
        vector_scores.append((row["chunk_id"], float(score)))
    vector_ranked = _ranked_ids(vector_scores, candidate_k)

    fused: dict[str, float] = {}
    bm25_map = dict(bm25_ranked)
    vector_map = dict(vector_ranked)
    for rank, (chunk_id, _) in enumerate(bm25_ranked, start=1):
        fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    for rank, (chunk_id, _) in enumerate(vector_ranked, start=1):
        fused[chunk_id] = fused.get(chunk_id, 0.0) + 0.8 / (RRF_K + rank)

    ranked = sorted(fused.items(), key=lambda item: item[1], reverse=True)[:top_k]
    items = []
    for rank, (chunk_id, rrf_score) in enumerate(ranked, start=1):
        chunk = chunk_by_id[chunk_id]
        items.append(
            {
                "rank": rank,
                "chunk_id": chunk_id,
                "document_id": chunk["document_id"],
                "document_title": chunk["document_title"],
                "section_title": chunk["section_title"],
                "page_start": chunk["page_start"],
                "page_end": chunk["page_end"],
                "content": chunk["content"],
                "rrf_score": round(rrf_score, 8),
                "bm25_score": round(bm25_map.get(chunk_id, 0.0), 6),
                "vector_score": round(vector_map.get(chunk_id, 0.0), 6),
                "safety_tags": chunk["safety_tags"],
            }
        )
    salient_terms = _salient_query_terms(cleaned_query)
    evidence_tokens: set[str] = set()
    evidence_texts: list[str] = []
    for item in items[:3]:
        evidence_text = f"{item['section_title'] or ''} {item['content']}"
        evidence_texts.append(evidence_text)
        evidence_tokens.update(_semantic_terms(evidence_text))
    evidence_joined = "\n".join(evidence_texts).lower()
    matched_terms = {
        term for term in salient_terms if term in evidence_tokens or term in evidence_joined
    }
    lexical_coverage = (
        len(matched_terms) / len(salient_terms) if salient_terms else 0.0
    )
    required_codes = {value.lower() for value in CODE_RE.findall(cleaned_query)}
    missing_codes = sorted(code for code in required_codes if code not in evidence_joined)

    return {
        "query": cleaned_query,
        "total": len(items),
        "embedding_backend": EMBEDDING_BACKEND,
        "embedding_model": EMBEDDING_MODEL,
        "diagnostics": {
            "salient_term_count": len(salient_terms),
            "matched_term_count": len(matched_terms),
            "lexical_coverage": round(lexical_coverage, 4),
            "missing_codes": missing_codes,
        },
        "items": items,
    }
