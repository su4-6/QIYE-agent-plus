#只管建表
from __future__ import annotations
#python自带数据库
import sqlite3
from pathlib import Path

from app.config import settings
#获取数据库连接
def get_connection() -> sqlite3.Connection:
    db_path = settings.database_path
    #确保 data/ 文件夹存在
    db_path.parent.mkdir(parents=True, exist_ok=True)
    #连接SQLite数据库
    connection = sqlite3.connect(db_path)
    #让查询结果支持用字段名访问，比如 row["ticket_id"]
    connection.row_factory = sqlite3.Row
    # 开启外键约束
    connection.execute("PRAGMA foreign_keys = ON")
    # WAL 模式：读写可以并发，不会因写锁导致读报错
    connection.execute("PRAGMA journal_mode = WAL")
    # 被锁时等待 5 秒而不是直接报 "database is locked"
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection
#初始化数据库
def init_database() -> None:
    Path("data").mkdir(exist_ok=True)
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                requester TEXT NOT NULL,
                category TEXT NOT NULL,
                priority TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                status TEXT NOT NULL,
                needs_human_approval INTEGER NOT NULL,
                answer TEXT NOT NULL,
                answer_source TEXT NOT NULL DEFAULT '本地知识库兜底',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        #这是一个简单的"数据库迁移"逻辑。这是为了兼容之前没有 answer_source 列的旧数据库。
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(tickets)").fetchall()
        }
        if "answer_source" not in columns:
            connection.execute(
                "ALTER TABLE tickets ADD COLUMN answer_source TEXT NOT NULL DEFAULT '本地知识库兜底'"
            )
            #创建 audit_logs 表（审计日志表）。记录对工单的所有操作。
            connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT NOT NULL,
                action TEXT NOT NULL,
                operator TEXT NOT NULL,
                detail TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id)
            )
            """
        )