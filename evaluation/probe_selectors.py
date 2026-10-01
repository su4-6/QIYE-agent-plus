"""User-authorized selector protocol probe: at most 10 attempts, no retries."""
import argparse
import hashlib
import json
from pathlib import Path

from app.config import settings
from app.knowledge import retrieve
from app.llm import generate_grounded_answer, last_generation
from app.metrics import percentiles
from evaluation.new_holdout import corpus_database, OUTPUT, NEW_CASES
from evaluation.run import ROOT, DATA, write

OLD_IDS = ["T02-Q2", "T12-Q2", "T26-Q1", "T28-Q2", "T34-Q1"]
NEW_IDS = ["NEW-01", "NEW-09", "NEW-17", "NEW-25", "NEW-33"]


def probe():
    if not settings.mimo_api_key.strip():
        raise RuntimeError("MIMO_API_KEY missing; no requests sent")
    cases = json.loads(DATA.read_text(encoding="utf-8"))["cases"] + json.loads(NEW_CASES.read_text(encoding="utf-8"))["cases"]
    by_id = {row["id"]: row for row in cases}
    selected = [by_id[value] for value in OLD_IDS + NEW_IDS]
    files = ["app/answer_validation.py", "app/llm.py", "evaluation/probe_selectors.py"]
    hashes = {name: hashlib.sha256((ROOT/name).read_text(encoding="utf-8").replace("\r\n","\n").encode()).hexdigest() for name in files}
    ledger_path = OUTPUT / "protocol-probe-ledger.json"
    raw_path = OUTPUT / "protocol-probe-raw.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {
        "authorization": "User explicitly approved up to 10 additional MiMo calls / 10000 output tokens / no retries",
        "call_limit": 10, "output_limit": 10000, "per_call_limit": 1000, "model": settings.mimo_model,
        "source_sha256_normalized_lf": hashes, "planned_ids": OLD_IDS + NEW_IDS, "attempt_ids": []}
    if ledger["source_sha256_normalized_lf"] != hashes or ledger["model"] != settings.mimo_model:
        raise ValueError("Protocol changed after requests started; do not restart budget")
    runs = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.exists() else []
    write(ledger_path, ledger)
    with corpus_database():
        object.__setattr__(settings, "llm_provider", "mimo")
        for case in selected:
            if case["id"] in ledger["attempt_ids"] or len(ledger["attempt_ids"]) >= 10:
                continue
            hits = retrieve(case["query"], "demo", mode="vector")["hits"][:4]
            ledger["attempt_ids"].append(case["id"])
            write(ledger_path, ledger)  # reserve before request; ambiguous attempts are never retried
            answer, cited = generate_grounded_answer(title=case["query"], description=case["query"],
                category="协议验证模拟问题", risk_level="低风险", hits=hits, allow_llm=True, tenant_id="demo")
            topics = [hit["title"].split()[1] for hit in hits if hit["id"] in cited]
            detail = last_generation.get()
            runs.append({"case_id": case["id"], "query": case["query"], "expected_topics": case["expected_topics"],
                "answer": answer, "citation_ids": cited, "selected_topics": topics,
                "source_topic_valid": bool(topics) and set(topics) <= set(case["expected_topics"]),
                "evidence": hits, **detail})
            write(raw_path, runs)
            if sum(row.get("usage", {}).get("completion_tokens", 0) for row in runs) > 10000:
                raise RuntimeError("Output budget exceeded; stopping")
    summary = {"attempts": len(ledger["attempt_ids"]), "completed_records": len(runs), "retry_count": 0,
        "completion_tokens": sum(row.get("usage", {}).get("completion_tokens", 0) for row in runs),
        "prompt_tokens": sum(row.get("usage", {}).get("prompt_tokens", 0) for row in runs),
        "structured_success": sum(bool(row.get("structured")) for row in runs),
        "source_render_success": sum(bool(row["answer"]) for row in runs),
        "selected_topic_valid": sum(row["source_topic_valid"] for row in runs),
        "latency_ms": percentiles([row["latency_ms"] for row in runs if "latency_ms" in row]),
        "limitations": ["Direct generator experiment; application release gate remains closed",
                        "Topic validity is simulated-label checking, not semantic entailment or human review",
                        "New questions were fixed before probe and are not used to retune the frozen retrieval model"]}
    write(OUTPUT / "protocol-probe-summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly permit paid requests within the saved 10-attempt budget")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live required; no requests sent")
    probe()
