"""Reproduce published frozen decisions without refitting thresholds or overwriting evidence."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from evaluation.new_holdout import OUTPUT, NEW_CASES, DATA, CORPUS, ROOT, corpus_database, collect, predict, fit_logistic, sha
from evaluation.run import write


def reproduce(output):
    if output.resolve() == OUTPUT.resolve():
        raise ValueError("Choose a separate reproduction output directory")
    frozen = json.loads((OUTPUT / "frozen-model.json").read_text(encoding="utf-8"))
    source_summary = json.loads((OUTPUT / "new-test-summary.json").read_text(encoding="utf-8"))
    dataset = json.loads(NEW_CASES.read_text(encoding="utf-8"))
    for name, expected in frozen["source_sha256_normalized_lf"].items():
        actual = hashlib.sha256((ROOT/name).read_text(encoding="utf-8").replace("\r\n", "\n").encode()).hexdigest()
        if actual != expected:
            raise ValueError("Frozen source differs: " + name)
    if sha(DATA) != frozen["cases_sha256"] or sha(CORPUS) != frozen["corpus_sha256"] or sha(NEW_CASES) != source_summary["new_cases_sha256"]:
        raise ValueError("Frozen input differs")
    original = json.loads(DATA.read_text(encoding="utf-8"))["cases"]
    with corpus_database():
        calibration = collect([case for case in original if case["split"] == "calibration"])
        rows = collect(dataset["cases"])
    weights = fit_logistic([row for row in calibration if row["kind"] != "high_risk"])
    weights_match = bool(np.allclose(weights, frozen["weights"], atol=1e-8, rtol=1e-8))
    results = {}
    for name, threshold in (("single_score", frozen["baseline_threshold"]), ("logistic", frozen["threshold"])):
        accepted = []
        for row in rows:
            score = row["evidence_score"] if name == "single_score" else predict(row["features"], frozen["weights"])
            if row["hit"] and not row["risk_human"] and score >= threshold:
                accepted.append(row)
        correct = sum(row["kind"] == "answerable" and row["top_correct"] for row in accepted)
        results[name] = {"accepted": len(accepted), "correct": correct}
        expected = source_summary["methods"][name]
        if results[name] != {key: expected[key] for key in ("accepted", "correct")}:
            raise ValueError("Published decision counts could not be reproduced: " + name)
    if not weights_match:
        raise ValueError("Training weights could not be reproduced")
    output.mkdir(parents=True, exist_ok=True)
    write(output / "retrieval-raw.json", rows)
    write(output / "verification.json", {"training_weights_match": weights_match, "frozen_decisions": results,
        "paid_calls": 0, "thresholds_refit": False, "source_summary_sha256": sha(OUTPUT / "new-test-summary.json")})
    print(json.dumps({"training_weights_match": weights_match, "frozen_decisions": results, "paid_calls": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation/results/heldout-reproduction")
    reproduce(parser.parse_args().output)
