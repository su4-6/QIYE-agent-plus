"""Replay saved model outputs through extractive alignment without making API calls."""
import argparse
import hashlib
import json
from pathlib import Path

from app.answer_validation import align_answer, sentences


def replay(source, output):
    raw_path = source / "generation-raw.json"
    records = json.loads(raw_path.read_text(encoding="utf-8"))
    review = json.loads((source / "semantic-review.json").read_text(encoding="utf-8"))
    fingerprint = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    if fingerprint != review["generation_raw_sha256"]:
        raise ValueError("AI review and model outputs do not match")
    verdicts = {row["case_id"]: row["verdict"] for row in review["rows"]}
    rows, controls = [], []
    for row in records:
        validation = align_answer(row.get("answer") or "", row["citations"], row["evidence"])
        accepted = bool(row.get("citations_valid")) and validation["passed"]
        rows.append({"case_id": row["case_id"], "original_citations_valid": row.get("citations_valid", False),
                     "ai_review": verdicts[row["case_id"]], "accepted": accepted, "validation": validation})
        if row.get("citations_valid"):
            source_hit = next(h for h in row["evidence"] if h["id"] in row["citations"])
            unit = next((s for s in sentences(source_hit["content"]) if s.endswith("。")), "")
            control = align_answer(unit, [source_hit["id"]], row["evidence"])
            controls.append({"case_id": row["case_id"], "quote": unit, "validation": control})
    unsupported = [row for row in rows if row["ai_review"] == "unsupported_detail"]
    supported = [row for row in rows if row["ai_review"] == "supported_core"]
    result = {"source_generation_sha256": fingerprint, "paid_calls": 0,
              "method": "Exact complete source sentence alignment; not semantic entailment/NLI",
              "summary": {"legacy_answers": len(rows), "accepted": sum(r["accepted"] for r in rows),
                  "ai_unsupported_blocked": sum(not r["accepted"] for r in unsupported),
                  "ai_unsupported_total": len(unsupported),
                  "ai_supported_core_blocked": sum(not r["accepted"] for r in supported),
                  "ai_supported_core_total": len(supported),
                  "source_quote_controls": len(controls), "source_quote_controls_passed": sum(c["validation"]["passed"] for c in controls)},
              "limitations": ["Replays old prompt outputs; the new extractive prompt has not been tested with fresh paid calls",
                              "Conservative rejection also blocks supported paraphrases",
                              "Copied source sentences can still be irrelevant, incomplete or malicious; manual release gate remains"],
              "rows": rows, "controls": controls}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=root / "evaluation/results/20261001")
    parser.add_argument("--output", type=Path, default=root / "evaluation/results/20261001-review/answer-replay.json")
    args = parser.parse_args()
    replay(args.source, args.output)
