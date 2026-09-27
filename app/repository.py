from __future__ import annotations

import json
from datetime import datetime, timezone

from app.database import get_connection


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_ticket(ticket: dict) -> None:
    now = utc_now()
    with get_connection() as db:
        db.execute("""INSERT INTO tickets(
            ticket_id,tenant_id,access_token_hash,title,description,requester,category,priority,
            risk_level,status,needs_human_approval,answer,answer_source,citations_json,
            retrieval_json,confidence,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            ticket["ticket_id"], ticket["tenant_id"], ticket["access_token_hash"],
            ticket["title"], ticket["description"], ticket["requester"], ticket["category"],
            ticket["priority"], ticket["risk_level"], ticket["status"],
            int(ticket["needs_human_approval"]), ticket["answer"], ticket["answer_source"],
            json.dumps(ticket.get("citations", []), ensure_ascii=False),
            json.dumps(ticket.get("retrieval", {}), ensure_ascii=False),
            float(ticket.get("confidence", 0)), now, now,
        ))
        _audit(db, ticket["ticket_id"], ticket["tenant_id"], "工单创建", "system", {
            "category": ticket["category"], "risk_level": ticket["risk_level"],
            "status": ticket["status"], "confidence": ticket.get("confidence", 0),
        })


def _audit(db, ticket_id: str, tenant_id: str, action: str, operator: str, detail: dict) -> None:
    db.execute("""INSERT INTO audit_logs(ticket_id,tenant_id,action,operator,detail,created_at)
        VALUES(?,?,?,?,?,?)""", (ticket_id, tenant_id, action, operator,
        json.dumps(detail, ensure_ascii=False), utc_now()))


def _decode(row) -> dict | None:
    if not row:
        return None
    item = dict(row)
    item["citations"] = json.loads(item.pop("citations_json", "[]"))
    item["retrieval"] = json.loads(item.pop("retrieval_json", "{}"))
    item["needs_human_approval"] = bool(item["needs_human_approval"])
    item.pop("access_token_hash", None)
    return item


def get_ticket(ticket_id: str, tenant_id: str) -> dict | None:
    with get_connection() as db:
        return _decode(db.execute(
            "SELECT * FROM tickets WHERE ticket_id=? AND tenant_id=?", (ticket_id, tenant_id)
        ).fetchone())


def get_ticket_with_secret(ticket_id: str, tenant_id: str):
    with get_connection() as db:
        return db.execute("SELECT * FROM tickets WHERE ticket_id=? AND tenant_id=?",
                          (ticket_id, tenant_id)).fetchone()


def list_tickets(tenant_id: str, skip: int = 0, limit: int = 50) -> list[dict]:
    with get_connection() as db:
        rows = db.execute("""SELECT * FROM tickets WHERE tenant_id=?
            ORDER BY created_at DESC LIMIT ? OFFSET ?""", (tenant_id, limit, skip)).fetchall()
        return [_decode(row) for row in rows]


def count_tickets(tenant_id: str) -> int:
    with get_connection() as db:
        return int(db.execute("SELECT COUNT(*) FROM tickets WHERE tenant_id=?", (tenant_id,)).fetchone()[0])


def approve_ticket(ticket_id: str, tenant_id: str, approved: bool, operator: str, comment: str) -> dict:
    target = "审批通过，待人工执行" if approved else "审批拒绝"
    answer = f"人工审批结果：{target}。审批人：{operator}。审批意见：{comment or '无'}"
    with get_connection() as db:
        db.execute("BEGIN IMMEDIATE")
        cur = db.execute("""UPDATE tickets SET status=?,answer=?,answer_source='人工审批',updated_at=?
            WHERE ticket_id=? AND tenant_id=? AND status='待人工处理'""",
            (target, answer, utc_now(), ticket_id, tenant_id))
        if cur.rowcount != 1:
            existing = db.execute("SELECT 1 FROM tickets WHERE ticket_id=? AND tenant_id=?",
                                  (ticket_id, tenant_id)).fetchone()
            raise LookupError("工单不存在" if not existing else "工单已处理或当前状态不允许审批")
        _audit(db, ticket_id, tenant_id, "人工审批", operator,
               {"approved": approved, "comment": comment, "target_status": target})
    result = get_ticket(ticket_id, tenant_id)
    assert result is not None
    return result


def list_audit_logs(ticket_id: str, tenant_id: str) -> list[dict]:
    with get_connection() as db:
        rows = db.execute("""SELECT id,ticket_id,action,operator,detail,created_at
            FROM audit_logs WHERE ticket_id=? AND tenant_id=? ORDER BY id""",
            (ticket_id, tenant_id)).fetchall()
        return [{**dict(row), "detail": json.loads(row["detail"])} for row in rows]


def list_documents(tenant_id: str) -> list[dict]:
    with get_connection() as db:
        return [dict(row) for row in db.execute("""SELECT id,title,version,format,active,created_at
            FROM knowledge_documents WHERE tenant_id=? ORDER BY created_at DESC""", (tenant_id,))]
