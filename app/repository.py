from __future__ import annotations

import json
from datetime import datetime

from app.database import get_connection

def utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"
#增删改查功能
#增加功能，保存工单
def save_ticket(ticket: dict) -> None:
    now = utc_now()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO tickets (
                ticket_id, title, description, requester, category, priority,
                risk_level, status, needs_human_approval, answer, answer_source,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket["ticket_id"],
                ticket["title"],
                ticket["description"],
                ticket["requester"],
                ticket["category"],
                ticket["priority"],
                ticket["risk_level"],
                ticket["status"],
                int(ticket["needs_human_approval"]),
                ticket["answer"],
                ticket.get("answer_source", "本地知识库兜底"),
                now,
                now,
            ),
        )
#增加，保存审计日志
def add_audit_log(
    ticket_id: str,
    action: str,
    operator: str,
    detail: dict,
    connection=None,
) -> None:
    owns_connection = connection is None
    connection = connection or get_connection()
    try:
        connection.execute(
            """
            INSERT INTO audit_logs (ticket_id, action, operator, detail, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ticket_id, action, operator, json.dumps(detail, ensure_ascii=False), utc_now()),
        )
    finally:
        if owns_connection:
            connection.close()


#查询单个工单
def get_ticket(ticket_id: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM tickets WHERE ticket_id = ?",
            (ticket_id,),
        ).fetchone()
        return dict(row) if row else None
#查询全部工单    
def list_tickets(skip: int = 0, limit: int = 50) -> list[dict]:
    """分页查询工单列表，按创建时间倒序。"""
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM tickets ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, skip),
        ).fetchall()
        return [dict(row) for row in rows]

def count_tickets() -> int:
    """返回工单总数，用于分页。"""
    with get_connection() as connection:
        row = connection.execute("SELECT COUNT(*) FROM tickets").fetchone()
        return int(row[0]) if row else 0
#更新工单状态
def update_ticket_status(
    ticket_id: str, status: str, answer: str, operator: str, detail: dict
) -> dict | None:
    now = utc_now()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE tickets
            SET status = ?, answer = ?, answer_source = ?, updated_at = ?
            WHERE ticket_id = ?
            """,
            (status, answer, "人工审批", now, ticket_id),
        )
        add_audit_log(ticket_id, "人工审批", operator, detail, connection=connection)
    return get_ticket(ticket_id)
#查询审计日志
def list_audit_logs(ticket_id: str) -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM audit_logs WHERE ticket_id = ? ORDER BY id ASC",
            (ticket_id,),
        ).fetchall()
        return [dict(row) for row in rows]
