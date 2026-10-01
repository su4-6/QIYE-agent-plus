import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone

from app.database import get_connection
from app.retrieval_health import vector_health
from app.config import settings
from app.evidence import policy, automation_enabled


def percentiles(values: list[float]) -> dict:
    ordered = sorted(values)
    return {f"p{p}": round(ordered[max(0, math.ceil(len(ordered)*p/100)-1)], 3) if ordered else None
            for p in (50, 95, 99)}


def retrieval_metrics(tenant: str, days: int) -> dict:
    since = (datetime.now(timezone.utc)-timedelta(days=days)).isoformat(timespec="seconds")
    with get_connection() as db:
        rows = db.execute("SELECT retrieval_json,needs_human_approval,handoff_reason FROM tickets WHERE tenant_id=? AND created_at>=?",
                          (tenant, since)).fetchall()
        totals = db.execute("""SELECT COUNT(*) total,
            COALESCE(SUM(status='待人工处理'),0) pending,
            COALESCE(SUM(risk_level='高风险'),0) high_risk FROM tickets WHERE tenant_id=?""", (tenant,)).fetchone()
    complete, legacy = [], 0
    for row in rows:
        try:
            result = json.loads(row["retrieval_json"])
        except (ValueError, TypeError):
            result = {}
        if not isinstance(result, dict) or result.get("schema_version") != 3:
            legacy += 1
        else:
            complete.append((row, result))
    n = len(complete)
    vector_runs = [r for _, r in complete if r.get("mode") != "bm25" and r.get("vector_state") != "disabled"]
    degraded = {"runtime_failed", "model_mismatch", "index_not_ready"}
    reasons = Counter(r.get("vector_reason") or r.get("vector_state") for r in vector_runs if r.get("vector_state") in degraded)
    rate = lambda count, denominator: count / denominator if denominator else None
    mode = "bm25" if settings.embedding_provider == "disabled" else policy().get("default_mode", "bm25")
    return {"days": days, "sample_count": n, "legacy_record_count": legacy, "window_total": len(rows),
        "automation": {"mode": mode, "enabled": automation_enabled(mode),
            "reason": policy().get("release_gate", {}).get(mode, {}).get("reason", "unvalidated")},
        "ticket_totals": dict(totals), "vector_sample_count": len(vector_runs),
        "zero_retrieval_rate": rate(sum(not r.get("hits") for _, r in complete), n),
        "vector_degradation_rate": rate(sum(r.get("vector_state") in degraded for r in vector_runs), len(vector_runs)),
        "insufficient_evidence_rate": rate(sum(not r.get("calibrated_sufficient", r.get("sufficient")) for _, r in complete), n),
        "evaluation_gate_count": sum(row["handoff_reason"] == "evaluation_gate" for row, _ in complete),
        "human_handoff_rate": rate(sum(bool(row["handoff_reason"]) for row, _ in complete), n),
        "degradation_reasons": dict(reasons),
        "latency_ms": percentiles([r["latency_ms"] for _, r in complete if isinstance(r.get("latency_ms"), (int, float))]),
        "vector": vector_health(tenant)}
