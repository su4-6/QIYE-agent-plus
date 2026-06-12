from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.agent import ticket_graph
from app.config import settings
from app.database import init_database
from app.llm import is_llm_enabled
from app.repository import (
    count_tickets,
    get_ticket,
    list_audit_logs,
    list_tickets,
    save_ticket,
    update_ticket_status,
)
from app.schemas import ApprovalRequest, AuditLogItem, TicketListItem, TicketRequest, TicketResponse

logger = logging.getLogger(__name__)

# HTML 模板文件路径
_INDEX_PATH = Path(__file__).parent / "templates" / "index.html"


def _加载首页() -> str:
    return _INDEX_PATH.read_text(encoding="utf-8")


def _工单详情转中文(ticket: dict) -> dict:
    return {
        "工单编号": ticket["ticket_id"],
        "工单标题": ticket["title"],
        "工单描述": ticket["description"],
        "提交人": ticket["requester"],
        "工单分类": ticket["category"],
        "优先级": ticket["priority"],
        "风险等级": ticket["risk_level"],
        "当前状态": ticket["status"],
        "处理建议": ticket["answer"],
        "回答来源": ticket.get("answer_source", "本地知识库兜底"),
        "是否需要人工审批": bool(ticket["needs_human_approval"]),
        "创建时间": ticket.get("created_at"),
        "更新时间": ticket.get("updated_at"),
    }


def _工单列表转中文(tickets: list[dict]) -> list[dict]:
    return [
        {
            "工单编号": ticket["ticket_id"],
            "工单标题": ticket["title"],
            "提交人": ticket["requester"],
            "工单分类": ticket["category"],
            "优先级": ticket["priority"],
            "风险等级": ticket["risk_level"],
            "当前状态": ticket["status"],
            "回答来源": ticket.get("answer_source", "本地知识库兜底"),
            "是否需要人工审批": bool(ticket["needs_human_approval"]),
            "创建时间": ticket["created_at"],
        }
        for ticket in tickets
    ]


def _解析审计详情(detail: str) -> dict | str:
    try:
        return json.loads(detail)
    except json.JSONDecodeError:
        return detail


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化数据库。"""
    init_database()
    yield


app = FastAPI(
    title="企业工单智能处理 Agent 系统",
    description="基于接口服务、流程编排、知识库检索、工具调用和人工审批的企业工单智能处理 Agent 系统",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url="/接口结构.json",
)


@app.get("/health", summary="服务健康检查", include_in_schema=False)
def health_check():
    return {
        "status": "ok",
        "app": settings.app_name,
        "env": settings.app_env,
        "llm_enabled": is_llm_enabled(),
    }


@app.get("/", response_class=HTMLResponse, summary="中文主界面")
def 首页():
    return HTMLResponse(_加载首页())

#这是整个项目最核心的 3 行代码
#payload: TicketRequest — FastAPI 自动把请求 JSON 转成 TicketRequest 对象并校验
@app.post("/工单", response_model=TicketResponse, summary="提交工单并触发智能处理")
def 提交工单(payload: TicketRequest):
#ticket_graph.invoke(payload.model_dump()) — 把 TicketRequest 转成字典，交给 LangGraph 流水线处理。invoke 是同步调用，会等所有 4 个节点执行完再返回
    try:
        result = ticket_graph.invoke(payload.model_dump())
    except Exception:
        logger.exception("工单处理失败")
        raise HTTPException(status_code=500, detail="工单处理失败，请联系管理员")
#save_ticket(result) — 把处理结果（已经包含 ticket_id、category、risk_level、answer 等）存入 SQLite
    save_ticket(result)
    return result


@app.get("/工单", summary="查看全部工单（支持分页）")
def 查看全部工单(
    skip: int = Query(0, ge=0, description="跳过的条数"),
    limit: int = Query(50, ge=1, le=200, description="每页条数"),
):
    tickets = _工单列表转中文(list_tickets(skip=skip, limit=limit))
    total = count_tickets()
    return {"工单列表": tickets, "总数": total, "跳过": skip, "每页": limit}


@app.get("/工单/{ticket_id}", summary="查看工单详情")
def 查看工单详情(ticket_id: str):
    ticket = get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在")
    return _工单详情转中文(ticket)


@app.get("/工单/{ticket_id}/审计日志", response_model=list[AuditLogItem], summary="查看工单审计日志")
def 查看审计日志(ticket_id: str):
    ticket = get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在")
    logs = list_audit_logs(ticket_id)
    return [
        {
            "id": log["id"],
            "ticket_id": log["ticket_id"],
            "action": log["action"],
            "operator": log["operator"],
            "detail": _解析审计详情(log["detail"]),
            "created_at": log["created_at"],
        }
        for log in logs
    ]


@app.get("/模型状态", summary="查看大模型配置状态")
def 查看模型状态():
    return {
        "是否启用大模型": is_llm_enabled(),
        "模型": settings.llm_model,
        "接口地址": settings.llm_base_url,
        "是否已配置密钥": bool(settings.llm_api_key.strip()),
    }

@app.post("/工单/{ticket_id}/审批", summary="人工审批高风险工单")
def 人工审批(ticket_id: str, payload: ApprovalRequest):
    ticket = get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在")

    if not ticket.get("needs_human_approval"):
        return {"提示": "该工单不需要人工审批", "工单": _工单详情转中文(ticket)}

    status = "审批通过，待执行" if payload.approved else "审批拒绝"
    answer = (
        f"工单 {ticket_id} 的人工审批结果：{status}。\n"
        f"审批人：{payload.operator}。\n"
        f"审批意见：{payload.comment or '无'}"
    )
    updated_ticket = update_ticket_status(
        ticket_id=ticket_id,
        status=status,
        answer=answer,
        operator=payload.operator,
        detail=payload.model_dump(by_alias=True),
    )
    return {"提示": "审批已处理", "工单": _工单详情转中文(updated_ticket)} # type: ignore


# ---- 英文兼容路径 ----

@app.post("/tickets", response_model=TicketResponse, include_in_schema=False)
def create_ticket(payload: TicketRequest):
    return 提交工单(payload)


@app.get("/tickets", include_in_schema=False)
def query_tickets(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return 查看全部工单(skip=skip, limit=limit)


@app.get("/tickets/{ticket_id}", include_in_schema=False)
def query_ticket(ticket_id: str):
    return 查看工单详情(ticket_id)


@app.get("/tickets/{ticket_id}/audit-logs", include_in_schema=False)
def query_audit_logs(ticket_id: str):
    return 查看审计日志(ticket_id)


@app.post("/tickets/{ticket_id}/approval", include_in_schema=False)
def approve_ticket(ticket_id: str, payload: ApprovalRequest):
    return 人工审批(ticket_id, payload)
