"""Check frozen inputs, experiment outputs and the separate 10-call ledger."""
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "evaluation/results/20261001-heldout"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify():
    frozen = read(OUTPUT / "frozen-model.json")
    dataset = read(ROOT / "evaluation/heldout-v3-cases.json")
    summary = read(OUTPUT / "new-test-summary.json")
    if dataset["frozen_model_sha256"] != sha(OUTPUT / "frozen-model.json"):
        raise ValueError("New questions are not bound to this frozen model")
    if summary["new_cases_sha256"] != sha(ROOT / "evaluation/heldout-v3-cases.json"):
        raise ValueError("New questions changed after test")
    if frozen["cases_sha256"] != sha(ROOT / "evaluation/benchmark-cases.json") or frozen["corpus_sha256"] != sha(ROOT / "data/simulated_sops.json"):
        raise ValueError("Original inputs changed")
    if datetime.fromisoformat(dataset["created_utc"]) <= datetime.fromisoformat(frozen["utc"]):
        raise ValueError("Questions were not saved after model freeze")
    for name, expected in frozen["source_sha256_normalized_lf"].items():
        actual = hashlib.sha256((ROOT/name).read_text(encoding="utf-8").replace("\r\n", "\n").encode()).hexdigest()
        if actual != expected:
            raise ValueError("Frozen feature code changed: " + name)
    ledger = read(OUTPUT / "protocol-probe-ledger.json")
    raw = read(OUTPUT / "protocol-probe-raw.json")
    if len(ledger["attempt_ids"]) > 10 or len(set(ledger["attempt_ids"])) != len(ledger["attempt_ids"]):
        raise ValueError("Invalid probe budget")
    if {row["case_id"] for row in raw} != set(ledger["attempt_ids"]):
        raise ValueError("Incomplete probe ledger")
    tokens = sum(row.get("usage", {}).get("completion_tokens", 0) for row in raw)
    if tokens > 10000 or any(row.get("usage", {}).get("completion_tokens", 0) > 1000 for row in raw):
        raise ValueError("Probe output budget exceeded")
    probe_summary = read(OUTPUT / "protocol-probe-summary.json")
    if probe_summary["attempts"] != len(ledger["attempt_ids"]) or probe_summary["completion_tokens"] != tokens:
        raise ValueError("Probe summary differs from raw budget records")
    review = read(OUTPUT / "protocol-semantic-review.json")
    if review["probe_raw_sha256"] != sha(OUTPUT / "protocol-probe-raw.json"):
        raise ValueError("AI review belongs to different probe records")
    for name, expected in ledger["source_sha256_normalized_lf"].items():
        actual = hashlib.sha256((ROOT/name).read_text(encoding="utf-8").replace("\r\n", "\n").encode()).hexdigest()
        if actual != expected:
            raise ValueError("Probe protocol source changed: " + name)
    tests = (OUTPUT / "engineering-tests.txt").read_text(encoding="utf-8-sig")
    matched = re.search(r"Ran (\d+) tests in ([0-9.]+)s\s+OK", tests)
    if not matched:
        raise ValueError("No successful test completion")
    verification = {"utc": datetime.now(timezone.utc).isoformat(), "tests_passed": int(matched[1]),
        "new_question_count": len(dataset["cases"]), "additional_model_attempts": len(ledger["attempt_ids"]),
        "additional_completion_tokens": tokens, "model_deployed": False,
        "logistic_adopted_as_default": False, "source_protocol": "source_selection_v2",
        "source_sha256_normalized_lf": {str(path.relative_to(ROOT)).replace("\\", "/"):
            hashlib.sha256(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode()).hexdigest()
            for folder in ("app", "evaluation", "scripts", "tests") for path in sorted((ROOT/folder).glob("*.py"))}}
    (OUTPUT / "delivery-validation.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {path.name: sha(path) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "manifest.json"}
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in verification.items() if key != "source_sha256_normalized_lf"}))


if __name__ == "__main__":
    verify()
