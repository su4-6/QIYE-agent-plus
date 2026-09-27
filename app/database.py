from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator

from app.config import settings


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    path = settings.database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA journal_mode=WAL")
    try:
        import sqlite_vec
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        db.enable_load_extension(False)
    except ImportError:
        pass
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _add_column(db: sqlite3.Connection, table: str, name: str, definition: str) -> None:
    names = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
    if name not in names:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def init_database() -> None:
    with get_connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS tickets (
            ticket_id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
            requester TEXT NOT NULL, category TEXT NOT NULL, priority TEXT NOT NULL,
            risk_level TEXT NOT NULL, status TEXT NOT NULL,
            needs_human_approval INTEGER NOT NULL, answer TEXT NOT NULL,
            answer_source TEXT NOT NULL DEFAULT '规则答复',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        for name, definition in (
            ("tenant_id", "TEXT NOT NULL DEFAULT 'legacy'"),
            ("access_token_hash", "TEXT NOT NULL DEFAULT ''"),
            ("citations_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("retrieval_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("confidence", "REAL NOT NULL DEFAULT 0"),
            ("answer_source", "TEXT NOT NULL DEFAULT '规则答复'"),
        ):
            _add_column(db, "tickets", name, definition)
        db.execute("""CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL DEFAULT 'legacy', action TEXT NOT NULL,
            operator TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL,
            FOREIGN KEY(ticket_id) REFERENCES tickets(ticket_id))""")
        _add_column(db, "audit_logs", "tenant_id", "TEXT NOT NULL DEFAULT 'legacy'")
        db.execute("""CREATE TABLE IF NOT EXISTS knowledge_documents (
            id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL, title TEXT NOT NULL,
            version INTEGER NOT NULL, format TEXT NOT NULL, sha256 TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
            UNIQUE(tenant_id,title,version))""")
        db.execute("""CREATE TABLE IF NOT EXISTS knowledge_chunks (
            id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL, tenant_id TEXT NOT NULL,
            title TEXT NOT NULL, content TEXT NOT NULL, search_tokens TEXT NOT NULL,
            position INTEGER NOT NULL, embedding BLOB, embedding_model TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(document_id) REFERENCES knowledge_documents(id))""")
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(search_tokens, tokenize='unicode61')")
        db.execute("""CREATE TABLE IF NOT EXISTS rate_limits (
            scope TEXT NOT NULL, bucket TEXT NOT NULL, count INTEGER NOT NULL,
            PRIMARY KEY(scope,bucket))""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_tickets_tenant_created ON tickets(tenant_id,created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_chunks_tenant_active ON knowledge_chunks(tenant_id,active)")
