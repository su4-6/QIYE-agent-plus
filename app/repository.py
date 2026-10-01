from __future__ import annotations

import json
from datetime import datetime, timezone

from app.database import get_connection
from app.tools import can_retry_assistance


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
        db.execute("UPDATE tickets SET evidence_score=?,handoff_reason=?,request_id=? WHERE ticket_id=?",
            (ticket.get("evidence_score"), ticket.get("handoff_reason", ""),
             ticket.get("request_id", ""), ticket["ticket_id"]))
        if ticket.get("public_answer"):
            db.execute("UPDATE tickets SET public_answer=? WHERE ticket_id=?", (ticket["public_answer"], ticket["ticket_id"]))
        if ticket.get("employee_id"):
            db.execute("UPDATE tickets SET employee_id=? WHERE ticket_id=?", (ticket["employee_id"], ticket["ticket_id"]))
        db.execute('UPDATE tickets SET request_kind=?,service_request_json=?,approval_passed=? WHERE ticket_id=?',
                   (ticket.get('request_kind','incident'),json.dumps(ticket.get('service_request') or {},ensure_ascii=False),
                    int(ticket.get('approval_passed',False)),ticket['ticket_id']))
        _audit(db, ticket["ticket_id"], ticket["tenant_id"], "工单创建", "system", {
            "category": ticket["category"], "risk_level": ticket["risk_level"],
            "status": ticket["status"], "confidence": ticket.get("confidence", 0),
            "evidence_score": ticket.get("evidence_score"), "handoff_reason": ticket.get("handoff_reason", ""),
            "request_id": ticket.get("request_id", ""),
        })
        routing=ticket.get('retrieval',{}).get('approval_policy',{})
        if routing.get('allowed') and not ticket['needs_human_approval']:
            _audit(db,ticket['ticket_id'],ticket['tenant_id'],'只读自助自动放行','Atlas Policy',routing)
        rule=ticket.get('retrieval',{}).get('service_approval')
        if rule:
            _audit(db,ticket['ticket_id'],ticket['tenant_id'],'服务申请自动决策','Atlas Policy',rule)


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
    item['service_request']=json.loads(item.pop('service_request_json','{}'))
    item["needs_human_approval"] = bool(item["needs_human_approval"])
    item.pop("access_token_hash", None)
    if not item.get("public_answer"):
        item["public_answer"] = ("工单已提交给 IT 服务台。你可以补充发生时间、错误截图或已尝试的操作，处理回复会显示在这里。"
                                 if item["status"] == "待人工处理" else
                                 "申请已通过，等待工作人员处理。" if item["status"] == "审批通过，待人工执行" else
                                 "本次申请未通过。你可以补充情况，重新联系服务台。" if item["status"] == "审批拒绝" else item["answer"])
    return item


def get_ticket(ticket_id: str, tenant_id: str) -> dict | None:
    with get_connection() as db:
        item = _decode(db.execute(
            "SELECT * FROM tickets WHERE ticket_id=? AND tenant_id=?", (ticket_id, tenant_id)
        ).fetchone())
        if item:
            item["messages"] = [dict(row) for row in db.execute(
                "SELECT id,actor,operator,body,created_at FROM ticket_messages WHERE ticket_id=? AND tenant_id=? ORDER BY created_at,id",
                (ticket_id, tenant_id))]
            reasons={'high_risk':'涉及敏感操作，需要审批','employee_request':'员工尝试后申请人工','insufficient_evidence':'资料不足，无法可靠建议',
                     'generation_or_citation_failed':'模型未提供有效回复','source_selection_failed':'引用校验未通过',
                     'daily_model_quota':'今日模型额度已用完','clarification_limit':'多轮补充后仍未解决',
                     'support_requires_it':'已尝试的自助步骤无效，需IT进一步处理',
                     'automatic_support_disabled':'自动自助已关闭，需要人工预审'}
            reasons.update({'service_policy_review':'申请超出自动审批范围，需要人工审批',
                            'service_scope_update':'获批申请有新增补充，需人工复核执行范围'})
            last=[m for m in item['messages'] if m['actor']=='employee'][-3:]
            item['handoff_summary']='问题：'+item['title']+'\n员工描述：'+item['description'][:800]+'\n接管原因：'+reasons.get(item['handoff_reason'], '等待 IT 跟进')
            if last:item['handoff_summary']+='\n最近补充：\n'+'\n'.join(m['body'][:300] for m in last)
            item['can_retry_ai']=can_retry_assistance(item,[m['body'] for m in item['messages'] if m['actor']=='employee'])
        return item


def get_ticket_with_secret(ticket_id: str, tenant_id: str):
    with get_connection() as db:
        return db.execute("SELECT * FROM tickets WHERE ticket_id=? AND tenant_id=?",
                          (ticket_id, tenant_id)).fetchone()


def _ticket_filter(tenant_id,status=None,keyword=None):
    clause="tenant_id=?";args=[tenant_id]
    if status:clause+=' AND status=?';args.append(status)
    if keyword:clause+=' AND (title LIKE ? OR ticket_id LIKE ? OR requester LIKE ?)';args.extend(['%'+keyword+'%']*3)
    return clause,args


def list_tickets(tenant_id: str, skip: int = 0, limit: int = 50, status=None, keyword=None) -> list[dict]:
    clause,args=_ticket_filter(tenant_id,status,keyword)
    with get_connection() as db:
        rows = db.execute("SELECT * FROM tickets WHERE "+clause+" ORDER BY created_at DESC LIMIT ? OFFSET ?", (*args, limit, skip)).fetchall()
        return [_decode(row) for row in rows]


def count_tickets(tenant_id: str,status=None,keyword=None) -> int:
    clause,args=_ticket_filter(tenant_id,status,keyword)
    with get_connection() as db:
        return int(db.execute("SELECT COUNT(*) FROM tickets WHERE "+clause,args).fetchone()[0])


def approve_ticket(ticket_id: str, tenant_id: str, approved: bool, operator: str, comment: str) -> dict:
    target = "审批通过，待人工执行" if approved else "审批拒绝"
    answer = f"人工审批结果：{target}。审批人：{operator}。审批意见：{comment or '无'}"
    with get_connection() as db:
        db.execute("BEGIN IMMEDIATE")
        cur = db.execute("""UPDATE tickets SET status=?,answer=?,answer_source='人工审批',updated_at=?,approval_passed=?,needs_human_approval=0,workflow_version=workflow_version+1,public_answer=?
            WHERE ticket_id=? AND tenant_id=? AND status='待人工处理'""",
            (target, answer, utc_now(), int(approved),
             ("申请已通过，等待工作人员处理。" if approved else "本次申请未通过。你可以重新打开工单并补充情况。")
             + ('\n审批意见：'+comment if comment else ''), ticket_id, tenant_id))
        if cur.rowcount != 1:
            existing = db.execute("SELECT 1 FROM tickets WHERE ticket_id=? AND tenant_id=?",
                                  (ticket_id, tenant_id)).fetchone()
            raise LookupError("工单不存在" if not existing else "工单已处理或当前状态不允许审批")
        _audit(db, ticket_id, tenant_id, "人工审批", operator,
               {"approved": approved, "comment": comment, "target_status": target})
    result = get_ticket(ticket_id, tenant_id)
    assert result is not None
    return result


def continue_assistance(ticket_id: str, tenant_id: str) -> dict:
    """Run the existing graph again with employee context, using the same ticket."""
    from app.agent import ticket_graph
    item=get_ticket(ticket_id,tenant_id)
    if not item or item['status']!='AI处理中':
        raise LookupError('工单已更新，请刷新')
    old_version=item['workflow_version']
    turns=sum(m['actor']=='ai' for m in item['messages'])
    if turns>=5:
        result={'status':'待人工处理','needs_human_approval':True,'handoff_reason':'clarification_limit',
                'answer':'多轮补充后仍未解决，交给 IT 继续处理。','answer_source':'AI多轮跟进后人工接管','citations':[],
                'public_answer':'已将问题、沟通记录与尝试情况交给 IT 服务台，你无需重新提交。','retrieval':item['retrieval']}
    else:
        employee_text=[m['body'] for m in item['messages'] if m['actor']=='employee'
                       and m['body']!='希望 AI 继续根据目前信息排查。']
        conversation=[{'role':m['actor'],'text':m['body']} for m in item['messages']
                      if m['body']!='希望 AI 继续根据目前信息排查。'][-8:]
        if item['answer_source'] in {'AI澄清问题','逐句引用对齐的检索答复','结合上下文的AI排查建议'}:
            if not any(m['role']=='ai' for m in conversation):
                conversation.insert(0,{'role':'ai','text':item['public_answer']})
        latest=employee_text[-1] if employee_text else item['description']
        # Triage sees employee facts only: assistant warnings are not user requests.
        description=(item['description']+'\n员工补充：'+'\n'.join(employee_text[-6:]))[-5000:]
        try:
            result=ticket_graph.invoke({'title':item['title'],'description':description,'initial_description':item['description'],'conversation':conversation,
                                       'requester':item['requester'],'tenant_id':tenant_id,'allow_llm':True,
                                       'retrieval_query':item['title']+' '+latest})
        except Exception:
            result={'status':'待人工处理','needs_human_approval':True,'handoff_reason':'generation_or_citation_failed',
                    'answer':'AI 跟进暂时失败。','public_answer':'回复暂时未能完成，已交给 IT 服务台跟进，你的补充已保留。',
                    'answer_source':'生成失败，人工接管','citations':[],'retrieval':item['retrieval']}
    public=result.get('public_answer') or (result['answer'] if result['status']!='待人工处理' else
            '目前无法给出可靠的下一步建议，已将问题和沟通记录交给 IT 服务台。')
    with get_connection() as db:
        db.execute('BEGIN IMMEDIATE')
        current=db.execute('SELECT workflow_version,status FROM tickets WHERE ticket_id=? AND tenant_id=?',(ticket_id,tenant_id)).fetchone()
        if not current or current['workflow_version']!=old_version or current['status']!='AI处理中':
            raise LookupError('工单已更新，请刷新')
        if (not any(m['actor']=='ai' for m in item['messages'])
                and item['answer_source'] in {'AI澄清问题', '逐句引用对齐的检索答复','结合上下文的AI排查建议'}):
            db.execute('INSERT INTO ticket_messages(ticket_id,tenant_id,actor,operator,body,created_at) VALUES(?,?,?,?,?,?)',
                       (ticket_id,tenant_id,'ai','Atlas AI',item['public_answer'],item['created_at']))
        db.execute("""UPDATE tickets SET status=?,answer=?,public_answer=?,answer_source=?,needs_human_approval=?,
                      handoff_reason=?,citations_json=?,retrieval_json=?,workflow_version=workflow_version+1,updated_at=?
                      ,risk_level=?,category=?,priority=?,evidence_score=?,confidence=?
                      WHERE ticket_id=? AND tenant_id=?""",
                   (result['status'],result['answer'],public,result['answer_source'],int(result.get('needs_human_approval',False)),
                    result.get('handoff_reason',''),json.dumps(result.get('citations',[]),ensure_ascii=False),
                    json.dumps(result.get('retrieval',{}),ensure_ascii=False),utc_now(),
                    result.get('risk_level',item['risk_level']),result.get('category',item['category']),result.get('priority',item['priority']),
                    result.get('evidence_score',item.get('evidence_score')),result.get('confidence',item['confidence']),ticket_id,tenant_id))
        db.execute('INSERT INTO ticket_messages(ticket_id,tenant_id,actor,operator,body,created_at) VALUES(?,?,?,?,?,?)',
                   (ticket_id,tenant_id,'ai','Atlas AI',public,utc_now()))
        _audit(db,ticket_id,tenant_id,'AI跟进','system',{'from':'AI处理中','to':result['status'],'source':result['answer_source']})
    return get_ticket(ticket_id,tenant_id)


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
