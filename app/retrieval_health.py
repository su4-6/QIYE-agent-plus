import logging
import struct
import threading

from app.config import settings
from app.database import get_connection
from app.embeddings import model_id, local_dimension

_runtime = {}
_lock = threading.Lock()


def record_vector(tenant: str, state: str, reason: str = "") -> None:
    with _lock:
        _runtime[(str(settings.database_path.resolve()), tenant, model_id())] = (state, reason)


def vector_health(tenant: str = "demo") -> dict:
    if settings.embedding_provider == "disabled":
        return {"state": "disabled", "coverage": 0, "total": 0, "model": model_id(), "reason": "configured_disabled"}
    try:
        dimension = 768 if settings.embedding_provider == "gemini" else local_dimension(settings.embedding_model)
    except (ValueError, ImportError):
        return {"state": "runtime_failed", "coverage": 0, "total": 0, "model": model_id(), "reason": "unsupported_embedding_model"}
    with get_connection() as db:
        row = db.execute("""SELECT COUNT(*) total,
            COALESCE(SUM(c.embedding IS NOT NULL),0) any_vectors,
            COALESCE(SUM(c.embedding IS NOT NULL AND c.embedding_model=? AND length(c.embedding)=?),0) compatible
            FROM knowledge_chunks c JOIN knowledge_documents d ON d.id=c.document_id
            WHERE c.tenant_id=? AND c.active=1 AND d.active=1""",
            (model_id(), dimension * 4, tenant)).fetchone()
        total, any_vectors, compatible = tuple(row)
        try:
            probe = struct.pack("2f", 1, 0)
            db.execute("SELECT vec_distance_cosine(?,?)", (probe, probe)).fetchone()
            extension = True
        except Exception as exc:
            extension = False
            logging.getLogger(__name__).warning("vector_extension_unavailable type=%s", type(exc).__name__)
    state, reason = "ready", ""
    if not extension:
        state, reason = "runtime_failed", "sqlite_vec_unavailable"
    elif not compatible:
        state, reason = ("model_mismatch", "reindex_required") if any_vectors else ("index_not_ready", "vectors_missing")
    elif compatible < total:
        state, reason = "index_not_ready", "partial_coverage"
    with _lock:
        recent = _runtime.get((str(settings.database_path.resolve()), tenant, model_id()))
    if state == "ready" and recent and recent[0] == "runtime_failed":
        state, reason = recent
    elif state == "ready" and recent is None:
        state, reason = "index_ready_unverified", "no_runtime_check"
    return {"state": state, "reason": reason, "coverage": compatible, "total": total,
            "coverage_ratio": compatible / total if total else 0, "model": model_id(),
            "extension_available": extension, "runtime_checked": recent is not None}
