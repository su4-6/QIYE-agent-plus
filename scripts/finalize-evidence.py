"""Validate saved evidence and refresh hashes without running a model or benchmark."""
import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def finalize(output):
    output = output.resolve()
    if not output.is_relative_to(ROOT / "evaluation" / "results"):
        raise ValueError("Output must be under evaluation/results")
    summary = read(output / "summary.json")
    ledger = read(output / "generation-ledger.json")
    generations = read(output / "generation-raw.json")
    review = read(output / "semantic-review.json")
    attempts = ledger["attempt_ids"]
    if len(attempts) > 30 or len(attempts) != len(set(attempts)):
        raise ValueError("Invalid model-call budget ledger")
    if {row["case_id"] for row in generations} != set(attempts):
        raise ValueError("Generation records do not match call ledger")
    tokens = sum(row.get("usage", {}).get("completion_tokens", 0) for row in generations)
    if tokens > 30000 or tokens != summary["generation"]["completion_tokens"]:
        raise ValueError("Invalid output-token budget")
    if review["generation_raw_sha256"] != digest(output / "generation-raw.json"):
        raise ValueError("Semantic review belongs to different model outputs")
    inputs = {
        "data/simulated_sops.json": "corpus_sha256",
        "evaluation/benchmark-cases.json": "cases_sha256",
    }
    for filename, key in inputs.items():
        if digest(ROOT / filename) != ledger[key] or digest(ROOT / filename) != summary["policy"][key]:
            raise ValueError("Dataset no longer matches measurement")
    tests = (output / "engineering-tests.txt").read_text(encoding="utf-8-sig")
    matched = re.search(r"Ran (\d+) tests in ([0-9.]+)s\s+OK", tests)
    if not matched:
        raise ValueError("No successful unittest completion in saved output")
    if summary["vector_health"]["coverage_ratio"] != 1:
        raise ValueError("Real-vector experiment is incomplete")
    for concurrency, metrics in summary["performance"]["concurrency"].items():
        rows = read(output / f"http-concurrency-{concurrency}.json")
        successes = sum(row.get("status") == 201 and row.get("verified") is True for row in rows)
        if len(rows) != metrics["requests"] or successes != metrics["successes"]:
            raise ValueError("HTTP records do not match summary")
    current_sources = {
        str(path.relative_to(ROOT)).replace("\\", "/"):
        hashlib.sha256(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode()).hexdigest()
        for folder in ("app", "evaluation", "scripts", "tests")
        for path in sorted((ROOT / folder).glob("*.py"))
    }
    measured_sources = {
        filename.replace("\\", "/"): value
        for filename, value in summary["environment"]["source_sha256_normalized_lf"].items()
    }
    changed = [name for name, value in measured_sources.items() if current_sources.get(name) != value]
    added = sorted(set(current_sources) - set(measured_sources))
    write(output / "delivery-validation.json", {
        "utc": datetime.now(timezone.utc).isoformat(),
        "engineering_tests": {"passed": int(matched[1]), "elapsed_seconds": float(matched[2])},
        "model_calls": len(attempts), "completion_tokens": tokens,
        "current_source_sha256_normalized_lf": current_sources,
        "changed_since_retrieval_performance_snapshot": changed,
        "additional_sources_since_snapshot": added,
        "chronology": [
            "The 30 paid calls preceded the final offline context-preflight and packaged-resource fixes.",
            "summary.json records the completed retrieval/performance rerun, reusing saved generation outputs.",
            "The original live-call source snapshot was not separately frozen; do not claim it matches final sources.",
            "Final offline unittest output includes the later fixes; no extra paid calls were made.",
        ],
        "validation_limits": {
            "docker_image_built": False, "server_deployed": False,
            "initial_performance_failure": "HTTP ReadError WinError 10053; exact cause not established; incomplete run excluded",
            "browser": "Admin login and empty metrics/day-window verified; final added copy checked statically only",
        },
        "commands": [
            "conda run -n ticket-agent python -m unittest discover -s tests -v",
            "conda run -n ticket-agent python -m evaluation.run --reuse-generation --performance --output evaluation/results/20261001",
            "conda run -n ticket-agent python scripts/finalize-evidence.py",
        ],
        "input_sha256": {filename: digest(ROOT / filename) for filename in inputs},
    })
    write(output / "manifest.json", {
        path.name: digest(path) for path in sorted(output.iterdir())
        if path.is_file() and path.name != "manifest.json"
    })
    print(json.dumps({"tests_passed": int(matched[1]), "model_calls": len(attempts),
                      "completion_tokens": tokens, "changed_sources": changed}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation/results/20261001")
    finalize(parser.parse_args().output)
