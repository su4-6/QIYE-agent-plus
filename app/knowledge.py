from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from app.database import get_connection, init_database
from app.embeddings import embed, model_id


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
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= limit:
            chunks.append(paragraph)
        else:
            for start in range(0, len(paragraph), limit - 50):
                chunks.append(paragraph[start:start + limit])
    return chunks


def import_document(tenant_id: str, title: str, fmt: str, text: str, *, with_embedding: bool = True) -> dict:
    title = title.strip()[:120]
    chunks = split_chunks(text)
    if not title or not chunks:
        raise ValueError("文档标题和内容不能为空")
    prepared = []
    for chunk in chunks:
        vector = embed(chunk, document=True) if with_embedding else None
        prepared.append((chunk, " ".join(tokens(chunk)), vector))
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
            row = db.execute("""INSERT INTO knowledge_chunks
                (document_id,tenant_id,title,content,search_tokens,position,embedding,embedding_model)
                VALUES(?,?,?,?,?,?,?,?)""",
                (cur.lastrowid, tenant_id, title, content, search_tokens, position, vector,
                 model_id() if vector else None))
            db.execute("INSERT INTO chunks_fts(rowid,search_tokens) VALUES(?,?)", (row.lastrowid, search_tokens))
    return {"document_id": cur.lastrowid, "version": version, "chunks": len(chunks)}


def seed_demo() -> None:
    with get_connection() as db:
        exists = db.execute("SELECT 1 FROM knowledge_documents WHERE tenant_id='demo' LIMIT 1").fetchone()
    if not exists:
        source = Path(__file__).resolve().parent.parent / "data" / "knowledge_base.txt"
        try:
            import_document("demo", "IT 服务台操作规程", "txt", source.read_text(encoding="utf-8"),
                            with_embedding=True)
        except (ImportError, RuntimeError, OSError):
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
    return len(updates)


def retrieve(query: str, tenant_id: str, limit: int = 5) -> dict:
    rewritten = rewrite_query(query)
    terms = list(dict.fromkeys(tokens(rewritten)))[:12]
    lexical: list[dict] = []
    with get_connection() as db:
        if terms:
            expression = " OR ".join('"' + t.replace('"', '') + '"' for t in terms)
            lexical = [dict(r) for r in db.execute("""SELECT c.id,c.document_id,c.title,c.content,
                d.version,bm25(chunks_fts) AS distance FROM chunks_fts
                JOIN knowledge_chunks c ON c.id=chunks_fts.rowid
                JOIN knowledge_documents d ON d.id=c.document_id
                WHERE chunks_fts MATCH ? AND c.tenant_id=? AND c.active=1 AND d.active=1
                ORDER BY distance LIMIT 15""", (expression, tenant_id))]
    semantic: list[dict] = []
    try:
        vector = embed(rewritten)
        if vector:
            with get_connection() as db:
                semantic = [dict(r) for r in db.execute("""SELECT c.id,c.document_id,c.title,c.content,
                    d.version,vec_distance_cosine(c.embedding,?) AS distance
                    FROM knowledge_chunks c JOIN knowledge_documents d ON d.id=c.document_id
                    WHERE c.tenant_id=? AND c.active=1 AND d.active=1
                    AND c.embedding IS NOT NULL AND c.embedding_model=?
                    ORDER BY distance LIMIT 15""", (vector, tenant_id, model_id()))]
    except Exception:
        semantic = []
    ranked: dict[int, dict] = {}
    for source, hits in (("bm25", lexical), ("vector", semantic)):
        for rank, hit in enumerate(hits, 1):
            item = ranked.setdefault(hit["id"], {**hit, "score": 0.0, "channels": [], "similarity": None})
            item["score"] += 1 / (60 + rank)
            item["channels"].append(source)
            if source == "vector":
                item["similarity"] = 1 - hit["distance"]
    query_terms = set(terms)
    for item in ranked.values():
        title_terms = set(tokens(item["title"]))
        content_terms = set(tokens(item["content"]))
        item["matched_terms"] = len(query_terms & (title_terms | content_terms))
        item["score"] += min(len(query_terms & title_terms), 3) * 0.002
        item["score"] += min(item["matched_terms"], 5) * 0.001
        item.pop("distance", None)
    ordered = sorted(ranked.values(), key=lambda x: (-x["score"], -x["matched_terms"], x["id"]))
    best_match = max((item["matched_terms"] for item in ordered), default=0)
    threshold = max(2, int(best_match * 0.5))
    filtered = [item for item in ordered if item["matched_terms"] >= threshold
                or (item.get("similarity") or 0) >= 0.75]
    hits = (filtered or ordered)[:limit]
    sufficient = bool(hits and (hits[0]["matched_terms"] >= 2
                      or any((h.get("similarity") or 0) >= 0.75 for h in hits)))
    return {"query": rewritten, "hits": hits, "sufficient": sufficient,
            "bm25_count": len(lexical), "vector_count": len(semantic),
            "bm25_ids": [item["id"] for item in lexical[:limit]],
            "vector_ids": [item["id"] for item in semantic[:limit]]}


if __name__ == "__main__":
    import sys
    init_database()
    seed_demo()
    if len(sys.argv) > 1 and sys.argv[1] == "reindex":
        print(json.dumps({"indexed": reindex()}, ensure_ascii=False))
