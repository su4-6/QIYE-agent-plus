"""Employee/IT conversation and guarded, transactional state transitions."""
from app.database import get_connection
from app.repository import get_ticket, utc_now, _audit
from app.config import settings


def change_ticket(ticket_id, tenant_id, actor, action, body="", expected_version=None, operator=None):
    body = body.strip()
    with get_connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM tickets WHERE ticket_id=? AND tenant_id=?", (ticket_id, tenant_id)).fetchone()
        if not row:
            raise LookupError("工单不存在")
        if expected_version is not None and row["workflow_version"] != expected_version:
            raise LookupError("工单已有更新，请刷新后再操作")
        status = row["status"]
        target = status
        owner = row["assigned_to"]
        if actor == "employee":
            if action == "message":
                if status == 'AI处理中':
                    raise LookupError('AI 正在回复，请稍后再补充')
                if status in {"已解决", "审批拒绝"}:
                    raise LookupError("请先重新打开工单，再补充问题")
                if not body:
                    raise ValueError("请输入补充内容")
                if status == "待补充信息": target = "处理中"
                if status in {'等待补充信息（AI）','已给出处理建议'}:target='AI处理中'
            elif action == "resolve" and status in {"已给出处理建议", "待员工确认"}:
                target = "已解决"
            elif action == "escalate" and status in {"已给出处理建议", "待员工确认", '等待补充信息（AI）'}:
                target = "待人工处理"
                body = body or "尝试后仍未解决，请 IT 服务台继续处理。"
            elif action == "reopen" and status in {"已解决", "审批拒绝"}:
                target = "待人工处理"
                body = body or "问题仍然存在，申请重新处理。"
            elif (action=='retry_ai' and settings.low_risk_assistance and status=='待人工处理'
                  and row['risk_level']=='低风险' and not row['assigned_to']):
                target='AI处理中'
                body=body or '希望 AI 继续根据目前信息排查。'
            else:
                raise LookupError("当前状态不允许此操作")
        elif actor == "admin":
            if status in {"已解决", "审批拒绝"}:
                raise LookupError("工单已关闭，等待员工重新打开")
            sensitive = row["risk_level"] in {"中风险", "高风险"}
            if action == "start":
                if sensitive and not row["approval_passed"]:
                    raise LookupError("敏感申请须先完成人工审批")
                if status not in {"待人工处理", "审批通过，待人工执行"}:
                    raise LookupError("当前状态不允许接单")
                target = "处理中"; owner = operator or "IT 服务台"
            elif action in {"reply", "request_info", "resolve"}:
                if not body:
                    raise ValueError("请填写员工可见的回复")
                if action == "resolve":
                    if sensitive and not row["approval_passed"]:
                        raise LookupError("敏感申请须先完成人工审批")
                    target = "待员工确认"
                elif action == "request_info":
                    if sensitive and not row["approval_passed"]:
                        raise LookupError("请先审批敏感申请；审批前可发送普通回复")
                    target = "待补充信息"
            else:
                raise LookupError("未知处理操作")
        else:
            raise ValueError("未知角色")
        approval=0 if actor=='employee' and action=='reopen' else row['approval_passed']
        db.execute("UPDATE tickets SET status=?,assigned_to=?,approval_passed=?,workflow_version=workflow_version+1,updated_at=? WHERE ticket_id=? AND tenant_id=?",
                   (target, owner, approval, utc_now(), ticket_id, tenant_id))
        if body:
            db.execute("INSERT INTO ticket_messages(ticket_id,tenant_id,actor,operator,body,created_at) VALUES(?,?,?,?,?,?)",
                       (ticket_id,tenant_id,actor,operator or actor,body,utc_now()))
        if actor=='employee' and action in {'escalate','reopen'}:
            db.execute("UPDATE tickets SET handoff_reason='employee_request' WHERE ticket_id=? AND tenant_id=?",(ticket_id,tenant_id))
        _audit(db,ticket_id,tenant_id,"工单流转",operator or actor,{"action":action,"from":status,"to":target})
    return get_ticket(ticket_id,tenant_id)
