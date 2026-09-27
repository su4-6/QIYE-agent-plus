from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from app.database import get_connection, init_database
from app.knowledge import reindex, retrieve, seed_demo
from app.rag import retrieve_context


CASES = json.loads((Path(__file__).parent / "cases.json").read_text(encoding="utf-8"))


def contains_expected(items: list[str], expected: str) -> bool:
    return any(expected in item for item in items[:3])


def chunks(ids: list[int]) -> list[str]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    with get_connection() as db:
        rows = db.execute(f"SELECT id,content FROM knowledge_chunks WHERE id IN ({placeholders})", ids).fetchall()
    mapping = {row["id"]: row["content"] for row in rows}
    return [mapping[value] for value in ids if value in mapping]


def evaluate() -> dict:
    init_database()
    seed_demo()
    try:
        reindex("demo")
    except Exception as exc:
        print(f"向量索引不可用，本次仅报告关键词与 BM25：{exc}")
    hits = {"legacy_keyword": 0, "bm25": 0, "vector": 0, "hybrid": 0}
    latency = []
    valid_citations = 0
    total_citations = 0
    for case in CASES:
        legacy = retrieve_context(case["query"], top_k=3)
        hits["legacy_keyword"] += contains_expected(legacy, case["expected"])
        start = time.perf_counter()
        result = retrieve(case["query"], "demo", limit=3)
        latency.append((time.perf_counter() - start) * 1000)
        bm25 = chunks(result["bm25_ids"])
        vector = chunks(result["vector_ids"])
        hybrid = [item["content"] for item in result["hits"]]
        hits["bm25"] += contains_expected(bm25, case["expected"])
        hits["vector"] += contains_expected(vector, case["expected"])
        hits["hybrid"] += contains_expected(hybrid, case["expected"])
        for item in result["hits"]:
            total_citations += 1
            valid_citations += int(bool(item["document_id"] and item["id"]))
    count = len(CASES)
    return {
        "cases": count,
        "recall_at_3": {name: round(value / count, 4) for name, value in hits.items()},
        "citation_integrity": round(valid_citations / total_citations, 4) if total_citations else 0,
        "latency_ms": {"median": round(statistics.median(latency), 2),
                       "p95": round(sorted(latency)[int(len(latency) * .95) - 1], 2)},
    }


if __name__ == "__main__":
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2))
