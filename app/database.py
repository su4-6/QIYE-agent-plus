from __future__ import annotations

import sqlite3
import logging
from datetime import datetime, timezone
from contextlib import contextmanager, closing
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
    except (ImportError, sqlite3.Error, OSError) as exc:
        logging.getLogger(__name__).warning("sqlite_vec_load_failed type=%s", type(exc).__name__)
    finally:
        db.enable_load_extension(False)
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
    # Online backup handles WAL safely; user_version makes repeated startup cheap.
    path = settings.database_path
    if path.exists():
        with closing(sqlite3.connect(path)) as source:
            version = source.execute("PRAGMA user_version").fetchone()[0]
            tables = source.execute("SELECT 1 FROM sqlite_master WHERE name='tickets'").fetchone()
            if tables and version < 6:
                backup_dir = path.parent / "backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
                with closing(sqlite3.connect(backup_dir / f"{path.stem}-{stamp}.db")) as backup:
                    source.backup(backup)
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
            ("evidence_score", "REAL"),
            ("handoff_reason", "TEXT NOT NULL DEFAULT ''"),
            ("request_id", "TEXT NOT NULL DEFAULT ''"),
            ("public_answer", "TEXT NOT NULL DEFAULT ''"),
            ("assigned_to", "TEXT NOT NULL DEFAULT ''"),
            ("workflow_version", "INTEGER NOT NULL DEFAULT 0"),
            ("approval_passed", "INTEGER NOT NULL DEFAULT 0"),
            ("employee_id", "TEXT NOT NULL DEFAULT ''"),
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
        _add_column(db, "knowledge_chunks", "heading_path", "TEXT NOT NULL DEFAULT ''")
        _add_column(db, "knowledge_chunks", "chunk_key", "TEXT NOT NULL DEFAULT ''")
        _add_column(db, "rate_limits", "expires_at", "INTEGER")
        # Known legacy buckets use different units. Unknown scopes are kept
        # conservatively for one day rather than compared as hourly integers.
        db.execute("""UPDATE rate_limits SET expires_at=CASE
            WHEN scope LIKE 'llm-day:%' THEN (CAST(bucket AS INTEGER)+1)*86400
            WHEN scope LIKE 'ticket-hour:%' OR scope LIKE 'admin-login-hour:%'
                THEN (CAST(bucket AS INTEGER)+1)*3600
            ELSE CAST(strftime('%s','now') AS INTEGER)+86400 END WHERE expires_at IS NULL""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_quota_expiry ON rate_limits(expires_at)")
        db.execute("DELETE FROM rate_limits WHERE expires_at <= CAST(strftime('%s','now') AS INTEGER)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_tickets_tenant_created ON tickets(tenant_id,created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_chunks_tenant_active ON knowledge_chunks(tenant_id,active)")
        db.execute("""CREATE TABLE IF NOT EXISTS ticket_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL, actor TEXT NOT NULL, body TEXT NOT NULL,
            created_at TEXT NOT NULL, FOREIGN KEY(ticket_id) REFERENCES tickets(ticket_id))""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_messages_ticket ON ticket_messages(tenant_id,ticket_id,id)")
        _add_column(db, 'ticket_messages', 'operator', "TEXT NOT NULL DEFAULT ''")
        db.execute("UPDATE tickets SET approval_passed=1 WHERE answer_source='人工审批' AND status='审批通过，待人工执行'")
        db.execute("""CREATE TABLE IF NOT EXISTS employee_accounts (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, username TEXT NOT NULL,
            display_name TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(tenant_id,username))""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_tickets_employee ON tickets(tenant_id,employee_id,created_at)")
        # Retire the withdrawn fixed-text branch without changing the recorded reply.
        rows = db.execute("SELECT ticket_id,tenant_id FROM tickets WHERE answer_source='限定范围的标准排查' AND status='已给出处理建议'").fetchall()
        for row in rows:
            db.execute("""UPDATE tickets SET status='待人工处理',needs_human_approval=1,
                public_answer='工单已交给 IT 服务台。此前显示的是预设提示，未由 AI 生成；请补充报错及发生时间，等待 IT 的具体回复。',
                workflow_version=workflow_version+1,updated_at=? WHERE ticket_id=?""",
                (datetime.now(timezone.utc).isoformat(timespec='seconds'),row['ticket_id']))
            db.execute("INSERT INTO audit_logs(ticket_id,tenant_id,action,operator,detail,created_at) VALUES(?,?,?,?,?,?)",
                (row['ticket_id'],row['tenant_id'],'撤回固定回复路径','system','{"reason":"restore_original_agent_flow"}',datetime.now(timezone.utc).isoformat(timespec='seconds')))
        db.execute("""UPDATE tickets SET status='待人工处理',handoff_reason='generation_or_citation_failed',
            needs_human_approval=1,workflow_version=workflow_version+1,
            public_answer='AI 跟进被中断，已保留你的补充并交给 IT 服务台继续处理。'
            WHERE status='AI处理中'""")
        db.execute("PRAGMA user_version=6")
