from __future__ import annotations

from typing import NotRequired, Required, TypedDict
#StateGraph — LangGraph 的核心类，定义状态机/工作流
from langgraph.graph import END, StateGraph
#大模型回答
from app.llm import generate_ticket_answer
#rag最后的检索函数
from app.rag import retrieve_context
#tools的几个功能
from app.tools import (
    classify_category,
    create_it_ticket,
    decide_approval,
    evaluate_priority,
    evaluate_risk_level,
)
#定义状态，这是工单处理流程中的"状态对象"。它贯穿整个流程，每一步都可以读取和添加字段。
class TicketState(TypedDict):
    title: Required[str]
    description: Required[str]
    requester: Required[str]
    category: NotRequired[str]
    priority: NotRequired[str]
    risk_level: NotRequired[str]
    ticket_id: NotRequired[str]
    retrieved_context: NotRequired[list[str]]
    needs_human_approval: NotRequired[bool]
    status: NotRequired[str]
    answer: NotRequired[str]
    answer_source: NotRequired[str]
#调用tool三个功能
def classify_node(state: TicketState) -> TicketState:
    category = classify_category(state["title"], state["description"])
    priority = evaluate_priority(state["title"], state["description"])
    risk_level = evaluate_risk_level(
        state["title"], state["description"], priority
    )
    return {
        "category": category,
        "priority": priority,
        "risk_level": risk_level,
    } # type: ignore
#调用rag检索
def retrieve_node(state: TicketState) -> TicketState:
    query = f"{state.get('category', '')} {state['title']} {state['description']}"
    return {"retrieved_context": retrieve_context(query, top_k=3)} # type: ignore
#调用tool生成单号
def action_node(state: TicketState) -> TicketState:
    return {
        "ticket_id": create_it_ticket(),
        "needs_human_approval": decide_approval(
            state.get("risk_level", "低风险")),
    } # type: ignore
#从状态中取出前面几步的结果，为生成答复做准备，一一对应rag的retrieved_context
def draft_answer_node(state: TicketState) -> TicketState:
    context = "\n".join(state.get("retrieved_context", []))
    ticket_id = state.get("ticket_id", "")
    category = state.get("category", "综合咨询")
    priority = state.get("priority", "低")
    risk_level = state.get("risk_level", "低风险")

    if state.get("needs_human_approval"):
        status = "待人工审批"
        fallback_answer = (
            f"已创建工单 {ticket_id}。\n"
            f"系统识别该工单属于「{category}」，优先级为「{priority}」，"
            f"风险等级为「{risk_level}」。\n"
            f"该问题可能影响企业安全、生产稳定性或关键业务，需要人工审批后继续处理。\n"
            f"参考知识：\n{context}"
        )
    else:
        status = "已给出处理建议"
        fallback_answer = (
            f"已创建工单 {ticket_id}。\n"
            f"系统识别该工单属于「{category}」，优先级为「{priority}」，"
            f"风险等级为「{risk_level}」。\n"
            f"可先按以下知识库建议处理；如仍未解决，再转人工。\n"
            f"参考知识：\n{context}"
        )

    llm_answer = generate_ticket_answer(
    title=state["title"],
    description=state["description"],
    category=category,
    priority=priority,
    risk_level=risk_level,
    needs_human_approval=bool(state.get("needs_human_approval")),
    context=context,
    )
    if llm_answer:
        return {
            "status": status,
            "answer": llm_answer,
            "answer_source": "大模型生成",
        } # type: ignore
    return {
            "status": status,
            "answer": fallback_answer,
            "answer_source": "本地知识库兜底",
        } # type: ignore
#构建工作流图，创建一个状态图（state graph），状态类型是 TicketState
#上面四个流程
def build_graph():
#状态图
    graph = StateGraph(TicketState)
#四个节点
    graph.add_node("工单分类", classify_node)
    graph.add_node("知识检索", retrieve_node)
    graph.add_node("工具执行", action_node)
    graph.add_node("生成答复", draft_answer_node)
#流程入口
    graph.set_entry_point("工单分类")
#edge（边）连接起来
    graph.add_edge("工单分类", "知识检索")
    graph.add_edge("知识检索", "工具执行")
    graph.add_edge("工具执行", "生成答复")
    graph.add_edge("生成答复", END)
    
    return graph.compile()
#创建全局唯一的工单处理流程实例。其他模块通过 from app.agent import ticket_graph 来使用它。
ticket_graph = build_graph()

#agent.py 是整个项目的"指挥中心"。它不处理具体业务（分类交给 tools.py，检索交给 rag.py，AI 交给 llm.py），它只负责"调度"——先做什么、后做什么、数据怎么流转。这叫编排（orchestration）。