from __future__ import annotations

import hashlib
import io
import json
import re
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.database import get_connection, init_database
from app.embeddings import embed, model_id
from app.config import settings
from app.evidence import evidence_score, threshold, policy, automation_enabled
from app.retrieval_health import vector_health, record_vector
from app.observability import request_id, event
from app.resources import resource_path
from app.retrieval_features import retrieval_features


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tokens(text: str) -> list[str]:
    import jieba
    return [item.casefold() for item in jieba.cut(text) if len(item.strip()) > 1
            and re.fullmatch(r"[\w\u4e00-\u9fff]+", item, re.UNICODE)]


def rewrite_query(text: str) -> str:
    aliases = {"vpn": "虚拟专用网络", "登不上": "无法登录", "打不了": "无法打印", "付不了": "支付失败"}
    normalized = re.sub(r"\s+", " ", text).strip()
    extra = [expanded for short, expanded in aliases.items() if short in normalized.casefold()]
    return f"{normalized} {' '.join(extra)}".strip()


def extract_text(name: str, raw: bytes) -> tuple[str, str]:
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError("文件超过 5 MB")
    suffix = Path(name).suffix.lower()
    if suffix in {".txt", ".md"}:
        text = raw.decode("utf-8-sig")
    elif suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        if len(reader.pages) > 30:
            raise ValueError("PDF 超过 30 页")
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
    else:
        raise ValueError("仅支持 TXT、Markdown 和文本 PDF")
    text = text.strip()
    if len(text) < 30:
        raise ValueError("文档没有足够的可提取文字")
    suspicious = ("ignore previous", "ignore all instructions", "忽略以上指令", "忽略之前指令",
                  "system prompt", "泄露密钥", "输出环境变量")
    lowered = text.casefold()
    if any(pattern in lowered for pattern in suspicious):
        raise ValueError("文档包含疑似提示注入内容，需要人工清理后再导入")
    return suffix.removeprefix("."), text


def split_chunks(text: str, limit: int = 350) -> list[str]:
    return [item[1] for item in chunk_sections(text, limit)]


def chunk_sections(text: str, limit: int = 350) -> list[tuple[str, str]]:
    if limit < 32:
        raise ValueError("分块长度至少 32")
    result, headings, lines = [], [], []

    def flush():
        path = " / ".join(h for _, h in headings)
        # Limit context so a very long heading cannot produce an oversized chunk.
        prefix = path[:min(100, limit // 3)] + "\n" if path else ""
        capacity = limit - len(prefix)
        for paragraph in re.split(r"\n\s*\n", "\n".join(lines)):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            units = re.findall(r"[^。！？!?\n]+[。！？!?]?|\n", paragraph)
            block = ""
            for sentence in units:
                if len(sentence) > capacity:
                    if block.strip():
                        result.append((path, prefix + block.strip()))
                        block = ""
                    for start in range(0, len(sentence), capacity):
                        result.append((path, prefix + sentence[start:start+capacity]))
                elif len(block) + len(sentence) > capacity:
                    result.append((path, prefix + block.strip()))
                    block = sentence
                else:
                    block += sentence
            if block.strip():
                result.append((path, prefix + block.strip()))
        lines.clear()

    for line in text.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            flush()
            depth = len(heading[1])
            headings[:] = [(d, h) for d, h in headings if d < depth]
            headings.append((depth, heading[2].strip()))
        else:
            lines.append(line)
    flush()
    return result


def import_document(tenant_id: str, title: str, fmt: str, text: str, *, with_embedding: bool = True) -> dict:
    title = title.strip()[:120]
    sections = chunk_sections(text)
    chunks = [content for _, content in sections]
    if not title or not chunks:
        raise ValueError("文档标题和内容不能为空")
    prepared = []
    for chunk in chunks:
        vector = embed(chunk, document=True) if with_embedding else None
        prepared.append((chunk, " ".join(tokens(title + " " + chunk)), vector))
    digest = hashlib.sha256(text.encode()).hexdigest()
    with get_connection() as db:
        version = db.execute(
            "SELECT COALESCE(MAX(version),0)+1 FROM knowledge_documents WHERE tenant_id=? AND title=?",
            (tenant_id, title),
        ).fetchone()[0]
        db.execute("UPDATE knowledge_documents SET active=0 WHERE tenant_id=? AND title=?", (tenant_id, title))
        db.execute("UPDATE knowledge_chunks SET active=0 WHERE tenant_id=? AND title=?", (tenant_id, title))
        cur = db.execute(
            "INSERT INTO knowledge_documents(tenant_id,title,version,format,sha256,created_at) VALUES(?,?,?,?,?,?)",
            (tenant_id, title, version, fmt, digest, now()),
        )
        for position, (content, search_tokens, vector) in enumerate(prepared):
            heading = sections[position][0]
            key = hashlib.sha256(f"{heading}\n{position}\n{content}".encode()).hexdigest()[:24]
            row = db.execute("""INSERT INTO knowledge_chunks
                (document_id,tenant_id,title,content,search_tokens,position,embedding,embedding_model,heading_path,chunk_key)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (cur.lastrowid, tenant_id, title, content, search_tokens, position, vector,
                 model_id() if vector else None, heading, key))
            db.execute("INSERT INTO chunks_fts(rowid,search_tokens) VALUES(?,?)", (row.lastrowid, search_tokens))
    if prepared and all(vector is not None for _, _, vector in prepared):
        record_vector(tenant_id, "ready", "document_embedding_verified")
    return {"document_id": cur.lastrowid, "version": version, "chunks": len(chunks)}


def seed_demo() -> None:
    with get_connection() as db:
        exists = db.execute("SELECT 1 FROM knowledge_documents WHERE tenant_id='demo' LIMIT 1").fetchone()
    if not exists:
        source = resource_path("knowledge_base.txt")
        try:
            import_document("demo", "IT 服务台操作规程", "txt", source.read_text(encoding="utf-8"),
                            with_embedding=settings.embedding_provider != "disabled")
        except Exception as exc:
            logging.getLogger(__name__).warning("seed_vector_degraded type=%s", type(exc).__name__)
            record_vector("demo", "runtime_failed", type(exc).__name__)
            import_document("demo", "IT 服务台操作规程", "txt", source.read_text(encoding="utf-8"),
                            with_embedding=False)


def reindex(tenant_id: str = "demo") -> int:
    updates = []
    with get_connection() as db:
        rows = db.execute("SELECT id,content FROM knowledge_chunks WHERE tenant_id=? AND active=1", (tenant_id,)).fetchall()
    for row in rows:
        updates.append((embed(row["content"], document=True), model_id(), row["id"], tenant_id))
    with get_connection() as db:
        db.executemany("UPDATE knowledge_chunks SET embedding=?,embedding_model=? WHERE id=? AND tenant_id=?", updates)
    if updates and all(item[0] is not None for item in updates):
        record_vector(tenant_id, "ready", "reindex_verified")
    return len(updates)


def bm25_hits(terms: list[str], tenant_id: str) -> list[dict]:
    if not terms:
        return []
    expression = " OR ".join('"' + t.replace('"', '') + '"' for t in terms)
    with get_connection() as db:
        return [dict(r) for r in db.execute("""SELECT c.id,c.document_id,c.tenant_id,c.title,c.content,c.heading_path,c.chunk_key,
            d.version,bm25(chunks_fts) AS distance FROM chunks_fts
            JOIN knowledge_chunks c ON c.id=chunks_fts.rowid
            JOIN knowledge_documents d ON d.id=c.document_id
            WHERE chunks_fts MATCH ? AND c.tenant_id=? AND c.active=1 AND d.active=1
            ORDER BY distance LIMIT 15""", (expression, tenant_id))]


def retrieve(query: str, tenant_id: str, limit: int = 5, *, mode: str | None = None,
             rewrite: bool = True, title_signal: bool = True, rerank: bool | None = None,
             collect_features: bool = False) -> dict:
    started = time.perf_counter()
    mode = mode or ("bm25" if settings.embedding_provider == "disabled" else policy().get("default_mode", "bm25"))
    if mode not in {"bm25", "vector", "hybrid"}:
        raise ValueError("未知检索模式")
    if rerank is None:
        rerank = mode == "hybrid"
    rewritten = rewrite_query(query) if rewrite else query
    terms = list(dict.fromkeys(tokens(rewritten)))[:12]
    lexical = bm25_hits(terms, tenant_id) if mode != "vector" else []
    semantic: list[dict] = []
    health = vector_health(tenant_id) if mode != "bm25" else {"state": "not_used", "reason": "bm25_mode"}
    vector_state, vector_reason = health["state"], health.get("reason", "")
    if mode != "bm25" and health.get("coverage", 0) > 0 and health.get("extension_available"):
        try:
            vector = embed(rewritten)
            with get_connection() as db:
                semantic = [dict(r) for r in db.execute("""SELECT c.id,c.document_id,c.tenant_id,c.title,c.content,c.heading_path,c.chunk_key,
                    d.version,vec_distance_cosine(c.embedding,?) AS distance
                    FROM knowledge_chunks c JOIN knowledge_documents d ON d.id=c.document_id
                    WHERE c.tenant_id=? AND c.active=1 AND d.active=1
                    AND c.embedding IS NOT NULL AND c.embedding_model=? AND length(c.embedding)=?
                    ORDER BY distance LIMIT 15""", (vector, tenant_id, model_id(), len(vector)))]
            vector_state = "ready" if semantic else "no_results"
            if health.get("coverage", 0) < health.get("total", 0):
                vector_state, vector_reason = "index_not_ready", "partial_coverage"
            else:
                vector_reason = ""
            record_vector(tenant_id, vector_state, vector_reason)
        except Exception as exc:
            vector_state, vector_reason = "runtime_failed", type(exc).__name__
            record_vector(tenant_id, vector_state, vector_reason)
            logging.getLogger(__name__).warning("vector_degraded request_id=%s type=%s", request_id.get(), type(exc).__name__)
    vector_fallback = mode == "vector" and (not semantic or vector_state in {
        "runtime_failed", "index_not_ready", "model_mismatch", "disabled"})
    if vector_fallback:
        lexical = bm25_hits(terms, tenant_id)
        semantic = []
        rerank = False
    # A shadow lexical request supplies agreement signals for the experiment;
    # it does not change the vector ranking or become an answer source.
    feature_lexical = bm25_hits(terms, tenant_id) if collect_features and mode == "vector" and not vector_fallback else lexical
    ranked: dict[int, dict] = {}
    for source, hits in (("bm25", lexical), ("vector", semantic)):
        for rank, hit in enumerate(hits, 1):
            item = ranked.setdefault(hit["id"], {**hit, "score": 0.0, "channels": [], "similarity": None})
            item["score"] += 1 / (60 + rank)
            item["channels"].append(source)
            if source == "vector":
                item["similarity"] = 1 - hit["distance"]
    query_terms = set(terms)
    evidence_mode = "bm25" if vector_fallback or (mode == "hybrid" and not semantic) else mode
    if mode == "hybrid" and not semantic:
        rerank = False
    for item in ranked.values():
        title_terms = set(tokens(item["title"]))
        content_terms = set(tokens(item["content"]))
        item["matched_terms"] = len(query_terms & (title_terms | content_terms))
        if rerank:
            if title_signal:
                item["score"] += min(len(query_terms & title_terms), 3) * 0.002
            item["score"] += min(item["matched_terms"], 5) * 0.001
        item.pop("distance", None)
    ordered = sorted(ranked.values(), key=lambda x: (-x["score"], -x["matched_terms"], x["id"]))
    best_match = max((item["matched_terms"] for item in ordered), default=0)
    match_threshold = max(2, int(best_match * 0.5))
    filtered = [item for item in ordered if item["matched_terms"] >= match_threshold
                or (item.get("similarity") or 0) >= 0.75]
    hits = (filtered or ordered)[:limit]
    score = evidence_score(hits, terms, semantic=mode != "bm25")
    calibrated_sufficient = bool(hits and score >= threshold(evidence_mode))
    enabled = automation_enabled(evidence_mode)
    sufficient = calibrated_sufficient and enabled
    elapsed = round((time.perf_counter() - started) * 1000, 3)
    event("retrieval", mode=mode, hits=len(hits), vector_state=vector_state, latency_ms=elapsed)
    result = {"query": rewritten, "hits": hits, "sufficient": sufficient,
            "schema_version": 3, "mode": mode, "evidence_mode": evidence_mode, "vector_fallback": vector_fallback,
            "evidence_score": score, "threshold": threshold(evidence_mode),
            "calibrated_sufficient": calibrated_sufficient, "automation_enabled": enabled,
            "vector_state": vector_state, "vector_reason": vector_reason, "latency_ms": elapsed,
            "request_id": request_id.get(), "query_terms": terms,
            "bm25_count": len(lexical), "vector_count": len(semantic),
            "bm25_ids": [item["id"] for item in lexical[:limit]],
            "vector_ids": [item["id"] for item in semantic[:limit]]}
    if collect_features:
        result["features"] = retrieval_features(hits, terms, feature_lexical, semantic)
    return result


if __name__ == "__main__":
    import sys
    init_database()
    seed_demo()
    if len(sys.argv) > 1 and sys.argv[1] == "reindex":
        print(json.dumps({"indexed": reindex()}, ensure_ascii=False))
