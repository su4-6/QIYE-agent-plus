"""Evidence scores are retrieval signals, not probabilities of correctness."""
import json
from pathlib import Path

POLICY_PATH = Path(__file__).resolve().parent.parent / "evaluation" / "policy.json"


def policy() -> dict:
    try:
        return json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"default_mode": "bm25", "thresholds": {"bm25": 1.01, "hybrid": 1.01}}


def threshold(mode: str) -> float:
    return float(policy().get("thresholds", {}).get(mode, 1.01))


def automation_enabled(mode: str) -> bool:
    # A calibrated threshold is experimental until it passes held-out validation.
    return bool(policy().get("release_gate", {}).get(mode, {}).get("passed", False))


def evidence_score(hits: list[dict], terms: list[str], *, semantic: bool = True) -> float:
    if not hits or not terms:
        return 0.0
    # Only the first ranked source can authorize generation. More unrelated hits
    # must never increase the score.
    hit = hits[0]
    coverage = hit.get("matched_terms", 0) / len(set(terms))
    similarity = (hit.get("similarity") or 0) if semantic else 0
    return round(max(0.0, min(1.0, max(coverage, similarity))), 6)
