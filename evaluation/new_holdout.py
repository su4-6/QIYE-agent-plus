"""Freeze an OOF-calibrated experiment before authoring/running new questions."""
import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import platform
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app.config import settings
from app.database import init_database
from app.knowledge import retrieve
from app.retrieval_features import FEATURE_NAMES
from app.retrieval_health import vector_health
from app.tools import evaluate_priority, evaluate_risk_level
from evaluation.run import ROOT, DATA, CORPUS, isolated_directory, write, calibrate

OUTPUT = ROOT / "evaluation/results/20261001-heldout"
NEW_CASES = ROOT / "evaluation/heldout-v3-cases.json"
FROZEN_SOURCES = ["app/knowledge.py", "app/retrieval_features.py", "evaluation/new_holdout.py"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sigmoid(values):
    return 1 / (1 + np.exp(-np.clip(values, -30, 30)))


def fit_logistic(rows):
    x = np.array([[1.0] + [row["features"][name] for name in FEATURE_NAMES] for row in rows])
    y = np.array([float(row["kind"] == "answerable" and row["top_correct"]) for row in rows])
    weights = np.zeros(x.shape[1])
    # Fixed optimizer and regularization, no hyperparameter search on held-out data.
    for _ in range(3000):
        regularizer = .05 * weights
        regularizer[0] = 0
        weights -= .1 * (x.T @ (sigmoid(x @ weights) - y) / len(y) + regularizer)
    return weights.tolist()


def predict(features, weights):
    return float(sigmoid(np.array([1.] + [features[name] for name in FEATURE_NAMES]) @ np.array(weights)))


@contextmanager
def corpus_database():
    previous = dict(vars(settings))
    try:
        with isolated_directory() as directory:
            object.__setattr__(settings, "database_url", str(directory / "holdout.db"))
            object.__setattr__(settings, "embedding_provider", "local")
            object.__setattr__(settings, "llm_provider", "disabled")
            init_database()
            spec = importlib.util.spec_from_file_location("holdout_import", ROOT / "scripts/import-demo.py")
            importer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(importer)
            importer.import_simulated()
            if vector_health()["coverage_ratio"] != 1:
                raise RuntimeError("Incomplete real-vector index")
            yield
    finally:
        for key, value in previous.items():
            object.__setattr__(settings, key, value)


def collect(cases):
    rows = []
    for case in cases:
        start = time.perf_counter()
        result = retrieve(case["query"], "demo", mode="vector", collect_features=True)
        priority = evaluate_priority(case["query"], case["query"])
        risk = evaluate_risk_level(case["query"], case["query"], priority)
        topics = [hit["title"].split()[1] for hit in result["hits"]]
        rows.append({**case, "features": result["features"], "evidence_score": result["evidence_score"],
                     "topics": topics, "hit": bool(topics), "vector_state": result["vector_state"],
                     "top_correct": bool(topics and topics[0] in case["expected_topics"]),
                     "risk_human": risk in {"中风险", "高风险"},
                     "latency_ms": (time.perf_counter() - start) * 1000})
    if any(row["vector_state"] != "ready" for row in rows):
        raise RuntimeError("Vector degradation invalidates this experiment")
    return rows


def freeze():
    if (OUTPUT / "frozen-model.json").exists() or NEW_CASES.exists():
        raise RuntimeError("Experiment already frozen or new questions exist; refusing to refit")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    cases = json.loads(DATA.read_text(encoding="utf-8"))["cases"]
    with corpus_database():
        rows = collect([case for case in cases if case["split"] == "calibration"])
    eligible = [row for row in rows if row["kind"] != "high_risk"]
    oof = []
    fold = lambda group: int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % 5
    for number in range(5):
        train = [row for row in eligible if fold(row["group"]) != number]
        valid = [row for row in eligible if fold(row["group"]) == number]
        weights = fit_logistic(train)
        oof.extend({**row, "evidence_score": predict(row["features"], weights), "fold": number} for row in valid)
    calibration = calibrate(oof)
    write(OUTPUT / "calibration-raw.json", rows)
    write(OUTPUT / "oof-raw.json", sorted(oof, key=lambda row: row["id"]))
    frozen = {
        "utc": datetime.now(timezone.utc).isoformat(), "model": "logistic_8signals_v1",
        "embedding_model": settings.embedding_model, "feature_names": FEATURE_NAMES,
        "weights": fit_logistic(eligible), "threshold": calibration["threshold"],
        "calibration": calibration, "baseline_threshold": .510423,
        "training_role": "Original calibration only; original test and later challenge excluded",
        "calibration_role": "Five folds grouped by topic/negative group; out-of-fold predictions only",
        "optimizer": {"steps": 3000, "learning_rate": .1, "l2": .05, "init": "zeros"},
        "release_rule": "accepted>=20 and precision>=.95; high risk always human; do not retune after new test",
        "cases_sha256": sha(DATA), "corpus_sha256": sha(CORPUS),
        "source_sha256_normalized_lf": {name: hashlib.sha256((ROOT/name).read_text(encoding="utf-8").replace("\r\n", "\n").encode()).hexdigest() for name in FROZEN_SOURCES},
        "environment": {"python": sys.version, "platform": platform.platform(),
                        "numpy": importlib.metadata.version("numpy"), "fastembed": importlib.metadata.version("fastembed")},
    }
    write(OUTPUT / "frozen-model.json", frozen)
    print(json.dumps({"frozen": True, "training_cases": len(eligible), "oof_calibration": calibration}))


def evaluate_new():
    frozen_path = OUTPUT / "frozen-model.json"
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    if sha(DATA) != frozen["cases_sha256"] or sha(CORPUS) != frozen["corpus_sha256"]:
        raise ValueError("Original inputs changed after freeze")
    for name, expected in frozen["source_sha256_normalized_lf"].items():
        actual = hashlib.sha256((ROOT/name).read_text(encoding="utf-8").replace("\r\n", "\n").encode()).hexdigest()
        if actual != expected:
            raise ValueError("Feature/scoring code changed after freeze: " + name)
    dataset = json.loads(NEW_CASES.read_text(encoding="utf-8"))
    if dataset["frozen_model_sha256"] != sha(frozen_path) or len(dataset["cases"]) != 60:
        raise ValueError("New questions do not match frozen model")
    if (OUTPUT / "new-test-raw.json").exists():
        raise RuntimeError("New test already consumed; do not relabel or retune this experiment")
    with corpus_database():
        rows = collect(dataset["cases"])
    result = {"model_sha256": sha(frozen_path), "new_cases_sha256": sha(NEW_CASES),
              "label_source": dataset["label_source"], "fit_used_new_cases": False, "methods": {}}
    for name in ("single_score", "logistic"):
        threshold = frozen["baseline_threshold"] if name == "single_score" else frozen["threshold"]
        for row in rows:
            score = row["evidence_score"] if name == "single_score" else predict(row["features"], frozen["weights"])
            row[name] = {"score": score, "accepted": row["hit"] and not row["risk_human"] and score >= threshold}
        accepted = [row for row in rows if row[name]["accepted"]]
        correct = sum(row["kind"] == "answerable" and row["top_correct"] for row in accepted)
        matrix = {"tp":0, "fp":0, "tn":0, "fn":0}
        for row in rows:
            gold_human = row["kind"] != "answerable" or not row["top_correct"]
            predicted_human = not row[name]["accepted"]
            matrix["tp" if gold_human and predicted_human else "fp" if predicted_human else "fn" if gold_human else "tn"] += 1
        result["methods"][name] = {"threshold": threshold, "accepted": len(accepted), "correct": correct,
            "precision": correct/len(accepted) if accepted else None, "coverage": len(accepted)/len(rows),
            "confusion_matrix": matrix, "release_passed": len(accepted)>=20 and correct/len(accepted)>=.95}
    positives = [row for row in rows if row["kind"] == "answerable"]
    result["retrieval"] = {"answerable": len(positives), "hit_at_3": sum(bool(set(row["expected_topics"]) & set(row["topics"][:3])) for row in positives)/len(positives)}
    result["utc"] = datetime.now(timezone.utc).isoformat()
    write(OUTPUT / "new-test-raw.json", rows)
    write(OUTPUT / "new-test-summary.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("freeze", "test"))
    args = parser.parse_args()
    freeze() if args.phase == "freeze" else evaluate_new()
