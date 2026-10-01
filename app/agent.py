from __future__ import annotations

from typing import Literal, NotRequired, Required, TypedDict

from langgraph.graph import END, StateGraph

from app.knowledge import retrieve
from app.llm import generate_grounded_answer, is_llm_enabled, last_generation
from app.tools import (classify_category, create_it_ticket, evaluate_priority, evaluate_risk_level,
                       evaluate_assistance_risk, decide_approval)
from app.observability import request_id
from app.config import settings
from app.security import use_quota
from app.constants import DEFAULT_CATEGORY


class TicketState(TypedDict):
    title: Required[str]
    description: Required[str]
    requester: Required[str]
    tenant_id: Required[str]
    allow_llm: Required[bool]
    category: NotRequired[str]
    priority: NotRequired[str]
    risk_level: NotRequired[str]
    ticket_id: NotRequired[str]
    retrieval: NotRequired[dict]
    citations: NotRequired[list[dict]]
    confidence: NotRequired[float]
    evidence_score: NotRequired[float]
    handoff_reason: NotRequired[str]
    request_id: NotRequired[str]
    needs_human_approval: NotRequired[bool]
    status: NotRequired[str]
    answer: NotRequired[str]
    answer_source: NotRequired[str]
    public_answer: NotRequired[str]
    retrieval_query: NotRequired[str]
    conversation: NotRequired[list[dict]]
    initial_description: NotRequired[str]
    request_kind: NotRequired[str]
    service_request: NotRequired[dict | None]
    approval_passed: NotRequired[bool]


def service_policy(state: TicketState) -> dict:
    if state.get('request_kind','incident')!='service':return {}
    from app.approvals import evaluate_request
    rule=evaluate_request(state['tenant_id'],state['service_request'])
    # A checkbox cannot override sensitive context elsewhere in the request.
    if rule['decision']=='approve' and evaluate_assistance_risk(state['title'],state['description'])!='低风险':
        rule={**rule,'decision':'manual','reason':'描述中涉及敏感操作，须由 IT 核对完整申请范围，不能仅按清单自动放行。'}
    decision=rule['decision']
    status={'approve':'审批通过，待人工执行','reject':'审批拒绝','manual':'待人工处理'}[decision]
    suffix=('审批已通过；IT 将确认库存或安装安排后执行。尚未交付或安装，完成后需你确认。'
            if decision=='approve' else '这次申请未获批准，不会执行。需要调整申请时可重新打开并联系 IT。'
            if decision=='reject' else '申请和具体信息已交给 IT，无需重复提交。')
    public=f"{rule['reason']}\n{suffix}\n依据：演示服务申请规则 v{rule['policy_version']}。"
    return {'ticket_id':create_it_ticket(),'category':'软件服务申请' if state['service_request']['service_type']=='software_install' else '设备借用申请',
            'priority':evaluate_priority(state['title'],state['description']),'risk_level':'中风险' if decision=='manual' else '低风险',
            'status':status,'needs_human_approval':decision=='manual','approval_passed':decision=='approve',
            'answer':public,'public_answer':public,'answer_source':'服务申请规则自动审批' if decision!='manual' else '服务申请待人工审批',
            'citations':[],'retrieval':{'service_approval':rule},'confidence':0,'evidence_score':None,
            'handoff_reason':'service_policy_review' if decision=='manual' else ''}


def route_service(state: TicketState) -> Literal['service','incident']:
    return 'service' if state.get('request_kind','incident')=='service' else 'incident'


def triage(state: TicketState) -> dict:
    category = classify_category(state["title"], state["description"])
    priority = evaluate_priority(state["title"], state["description"])
    risk=(evaluate_assistance_risk(state['title'],state['description']) if settings.low_risk_assistance
          else evaluate_risk_level(state['title'],state['description'],priority))
    return {"category": category, "priority": priority,"risk_level":risk}


def search(state: TicketState) -> dict:
    query = state.get('retrieval_query') or f"{state['title']} {state['description']}"
    return {"retrieval": retrieve(query, state["tenant_id"], limit=5)}


def decide(state: TicketState) -> dict:
    result = state["retrieval"]
    hits = result["hits"]
    high_risk = state["risk_level"] in {"中风险", "高风险"}
    score = result["evidence_score"]
    # This opt-in permits read-only assistance in the personal demo, not execution
    # or automatic closure. The held-out release report remains unchanged.
    assistance = (settings.low_risk_assistance and not decide_approval(state['risk_level']) and bool(hits)
                  and state['category'] != DEFAULT_CATEGORY
                  and is_llm_enabled() and state['allow_llm'])
    needs_human = high_risk or not (result["sufficient"] or assistance)
    if settings.low_risk_assistance and decide_approval(state['risk_level']):needs_human=True
    citations = [{"chunk_id": h["id"], "document_id": h["document_id"], "title": h["title"],
                  "version": h["version"], "chunk_key": h.get("chunk_key", ""),
                  "heading_path": h.get("heading_path", ""), "excerpt": h["content"][:180]} for h in hits[:4]]
    if settings.low_risk_assistance:
        result={**result,'approval_policy':{'kind':'read_only_self_service','allowed':not decide_approval(state['risk_level']),
                  'reason':'低风险故障可自动进入只读AI自助；不授予权限、不执行维修',
                  'policy_version':'self-service-v1'}}
    return {"ticket_id": create_it_ticket(), "confidence": score, "evidence_score": score,"retrieval":result,
            "request_id": request_id.get(),
            "handoff_reason": "high_risk" if high_risk else "automatic_support_disabled" if settings.low_risk_assistance and not settings.auto_approve_low_risk else "evaluation_gate" if needs_human and result.get("calibrated_sufficient") and not result.get("automation_enabled") else "insufficient_evidence" if needs_human else "",
            "needs_human_approval": needs_human, "citations": citations}


def route_after_evidence(state: TicketState) -> Literal["human", "generate"]:
    return "human" if state["needs_human_approval"] else "generate"


def human_handoff(state: TicketState) -> dict:
    hits = state["retrieval"]["hits"]
    if not hits:
        text = "未检索到足够的企业知识，系统已转交人工处理。请补充影响范围、错误信息和发生时间。"
    else:
        suggestions = "\n".join(f"{i}. {hit['content']}" for i, hit in enumerate(hits[:3], 1))
        notice = ("当前评测尚未通过自动建议的质量门槛，本次工单先由人工审核。"
                  if state.get("handoff_reason") == "evaluation_gate" else "该工单需要人工确认。")
        text = f"{notice}以下仅为审批前排查资料：\n{suggestions}"
    return {"status": "待人工处理", "answer": text, "answer_source": "人工接管前知识库资料"}


def answer(state: TicketState) -> dict:
    hits = state["retrieval"]["hits"]
    allowed = state["allow_llm"]
    if allowed and is_llm_enabled():
        allowed = use_quota("llm-day", state["tenant_id"], settings.max_llm_daily, 86400)
        if not allowed:
            return {"status": "待人工处理", "needs_human_approval": True,
                    "handoff_reason": "daily_model_quota", "answer": "今日模型额度已用完，已转交人工处理。",
                    "answer_source": "模型额度不足，人工接管"}
    # With weak evidence the model may only ask questions, not publish steps.
    last_generation.set({})
    generated, cited = generate_grounded_answer(
        title=state["title"], description=state.get('initial_description',state["description"]), category=state["category"],
        risk_level=state["risk_level"], hits=hits[:4], allow_llm=allowed,
        tenant_id=state["tenant_id"],
        conversation=state.get('conversation',[]),
    )
    validation = last_generation.get().get("answer_validation")
    retrieval = {**state["retrieval"]}
    if validation:
        retrieval["answer_validation"] = validation
    generation = last_generation.get()
    if generation.get('handoff'):
        return {'status':'待人工处理','needs_human_approval':True,'handoff_reason':'support_requires_it',
                'answer':generation['public_answer'],'public_answer':generation['public_answer'],
                'answer_source':'AI判断需人工协助','citations':[], 'retrieval':retrieval}
    questions = generation.get('questions', [])
    if questions and settings.low_risk_assistance:
        text = generation.get('public_answer') or ('为了给出适合你情况的建议，请补充：\n' + '\n'.join(f'{i}. {q}' for i,q in enumerate(questions,1)))
        retrieval['assistant_questions']=questions
        return {'status':'等待补充信息（AI）','answer':text,'public_answer':text,
                'answer_source':'AI澄清问题','needs_human_approval':False,'citations':[], 'retrieval':retrieval}
    if generated and settings.low_risk_assistance and not state['retrieval'].get('calibrated_sufficient',state['retrieval'].get('sufficient',False)):
        generated=None
        retrieval['answer_validation']={'passed':False,'reason':'evidence_too_weak_for_advice'}
    if generated:
        citations = [item for item in state["citations"] if item["chunk_id"] in cited]
        return {"status": "已给出处理建议",
                "answer": generated, "answer_source": '结合上下文的AI排查建议' if generation.get('support_plan') else "逐句引用对齐的检索答复", "citations": citations,
                "public_answer": generated if generation.get('support_plan') else '可以先尝试以下排查建议。完成后，请确认是否恢复；仍有问题可继续补充或联系 IT。\n'+generated,
                "retrieval": retrieval}
    suggestions = "\n".join(f"{i}. {hit['content']}" for i, hit in enumerate(hits[:3], 1))
    if state["allow_llm"] and is_llm_enabled():
        return {"status": "待人工处理", "needs_human_approval": True,
                "handoff_reason": "source_selection_failed" if validation and not validation["passed"] else "generation_or_citation_failed",
                "answer": f"模型生成或引用校验未通过，已转交人工处理。以下为检索资料：\n{suggestions}",
                "answer_source": "模型校验失败，人工接管", "retrieval": retrieval}
    return {"status": "已给出处理建议", "answer": f"可先依据以下知识库资料排查：\n{suggestions}",
            "answer_source": "可追溯知识库答复"}


def build_graph():
    graph = StateGraph(TicketState)
    graph.add_node('服务申请策略判断',service_policy)
    graph.add_node("意图与风险识别", triage)
    graph.add_node("查询改写与混合召回", search)
    graph.add_node("证据与人工转接判断", decide)
    graph.add_node("人工接管", human_handoff)
    graph.add_node("生成与引用校验", answer)
    graph.set_entry_point('服务申请策略判断')
    graph.add_conditional_edges('服务申请策略判断',route_service,{'service':END,'incident':'意图与风险识别'})
    graph.add_edge("意图与风险识别", "查询改写与混合召回")
    graph.add_edge("查询改写与混合召回", "证据与人工转接判断")
    graph.add_conditional_edges("证据与人工转接判断", route_after_evidence,
                                {"human": "人工接管", "generate": "生成与引用校验"})
    graph.add_edge("人工接管", END)
    graph.add_edge("生成与引用校验", END)
    return graph.compile()


ticket_graph = build_graph()
