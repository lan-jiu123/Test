"""使用人工评测集计算 Top-K 页码命中率。"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.database import get_connection, init_database  # noqa: E402
from app.services.retrieval_service import hybrid_search, index_document  # noqa: E402


DATASET_PATH = PROJECT_ROOT / "evaluation" / "rag_questions_excel.csv"
REPORT_PATH = PROJECT_ROOT / "evaluation" / "retrieval_report.json"


def expected_pages(row: dict) -> set[int]:
    start = int(row["pdf_page_start"])
    end = int(row["pdf_page_end"] or start)
    return set(range(start, end + 1))


def result_pages(result: dict, top_k: int) -> set[int]:
    pages: set[int] = set()
    for item in result["items"][:top_k]:
        pages.update(range(item["page_start"], item["page_end"] + 1))
    return pages


def main() -> None:
    init_database()
    with get_connection() as connection:
        document = connection.execute(
            "SELECT id, title FROM documents WHERE status = 'ready' ORDER BY created_at LIMIT 1"
        ).fetchone()
    if document is None:
        raise SystemExit("没有 ready 状态的文档")

    index_result = index_document(document["id"])
    with DATASET_PATH.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = [row for row in csv.DictReader(stream) if row["answerable"] == "yes"]

    details = []
    hit_at_1 = hit_at_3 = hit_at_5 = 0
    for row in rows:
        result = hybrid_search(row["question"], document_id=document["id"], top_k=5)
        expected = expected_pages(row)
        flags = {
            "hit_at_1": bool(expected & result_pages(result, 1)),
            "hit_at_3": bool(expected & result_pages(result, 3)),
            "hit_at_5": bool(expected & result_pages(result, 5)),
        }
        hit_at_1 += flags["hit_at_1"]
        hit_at_3 += flags["hit_at_3"]
        hit_at_5 += flags["hit_at_5"]
        details.append(
            {
                "id": row["id"],
                "question": row["question"],
                "expected_pages": sorted(expected),
                "retrieved_pages": [
                    [item["page_start"], item["page_end"]] for item in result["items"]
                ],
                **flags,
            }
        )

    total = len(rows)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "document_id": document["id"],
        "document_title": document["title"],
        "index": index_result,
        "question_count": total,
        "metrics": {
            "hit_at_1": hit_at_1 / total if total else 0,
            "hit_at_3": hit_at_3 / total if total else 0,
            "hit_at_5": hit_at_5 / total if total else 0,
        },
        "details": details,
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"question_count": total, **report["metrics"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
