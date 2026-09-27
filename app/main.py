from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import HTMLResponse

from app.agent import ticket_graph
from app.config import settings
from app.database import init_database
from app.knowledge import extract_text, import_document, seed_demo
from app.llm import llm_status
from app.repository import (approve_ticket, count_tickets, get_ticket, get_ticket_with_secret,
                            list_audit_logs, list_documents, list_tickets, save_ticket)
from app.schemas import ApprovalRequest, HealthResponse, LoginRequest, TicketRequest, TicketResponse
from app.security import (admin_claims, check_access_token, check_password, create_session,
                          new_access_token, use_quota, validate_production_config, verify_turnstile)

logger = logging.getLogger(__name__)
INDEX_PATH = Path(__file__).parent / "templates" / "index.html"
ADMIN_PATH = Path(__file__).parent / "templates" / "admin.html"
DEMO_TENANT = "demo"


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_production_config()
    init_database()
    seed_demo()
    yield


app = FastAPI(title=settings.app_name, version="2.0.0", lifespan=lifespan,
              docs_url="/api/docs" if settings.app_env != "production" else None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline' https://challenges.cloudflare.com; "
        "frame-src https://challenges.cloudflare.com; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self' https://challenges.cloudflare.com"
    )
    return response


def public_ticket(ticket_id: str, token: str) -> dict:
    row = get_ticket_with_secret(ticket_id, DEMO_TENANT)
    if not row or not check_access_token(token, row["access_token_hash"]):
        raise HTTPException(status_code=404, detail="工单不存在或访问凭证无效")
    result = get_ticket(ticket_id, DEMO_TENANT)
    assert result is not None
    return result


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home():
    return HTMLResponse(INDEX_PATH.read_text(encoding="utf-8"))


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_page():
    return HTMLResponse(ADMIN_PATH.read_text(encoding="utf-8"))


@app.get("/health", response_model=HealthResponse, include_in_schema=False)
def health():
    retrieval = "bm25" if settings.embedding_provider == "disabled" else "bm25+vector"
    model = llm_status()
    return {"status": "ok", "database": "ok", "retrieval": retrieval,
            "llm_enabled": model["enabled"], "llm_provider": model["provider"],
            "llm_model": model["model"]}


@app.get("/api/v1/public-config")
def public_config():
    return {"turnstile_site_key": settings.turnstile_site_key if settings.app_env == "production" else ""}


@app.post("/api/v1/tickets", response_model=TicketResponse, status_code=201)
def create_ticket(payload: TicketRequest, request: Request):
    verify_turnstile(payload.turnstile_token)
    client_ip = request.client.host if request.client else "unknown"
    if not use_quota("ticket-hour", client_ip, settings.max_public_hourly, 3600):
        raise HTTPException(status_code=429, detail="提交过于频繁，请稍后再试")
    llm_allowed = use_quota("llm-day", DEMO_TENANT, settings.max_llm_daily, 86400)
    state = payload.model_dump(exclude={"turnstile_token"})
    result = ticket_graph.invoke({**state, "tenant_id": DEMO_TENANT, "allow_llm": llm_allowed})
    token, token_hash = new_access_token()
    stored = {**result, "tenant_id": DEMO_TENANT, "access_token_hash": token_hash}
    save_ticket(stored)
    response = get_ticket(result["ticket_id"], DEMO_TENANT)
    assert response is not None
    return {**response, "access_token": token}


@app.get("/api/v1/tickets/{ticket_id}", response_model=TicketResponse)
def read_ticket(ticket_id: str, x_ticket_token: str = Header(default="")):
    return public_ticket(ticket_id, x_ticket_token)


@app.post("/api/v1/admin/login")
def admin_login(payload: LoginRequest, response: Response, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if not use_quota("admin-login-hour", client_ip, 10, 3600):
        raise HTTPException(status_code=429, detail="登录尝试过多，请稍后再试")
    if not check_password(payload.password):
        raise HTTPException(status_code=401, detail="密码错误")
    session, csrf = create_session(DEMO_TENANT)
    response.set_cookie("ticket_session", session, httponly=True, secure=settings.app_env == "production",
                        samesite="strict", max_age=8 * 3600)
    return {"csrf_token": csrf, "expires_in": 8 * 3600}


@app.post("/api/v1/admin/logout")
def admin_logout(request: Request, response: Response):
    admin_claims(request, csrf=True)
    response.delete_cookie("ticket_session")
    return {"ok": True}


@app.get("/api/v1/admin/tickets")
def admin_tickets(request: Request, skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    claims = admin_claims(request)
    tenant = claims["tenant_id"]
    return {"items": list_tickets(tenant, skip, limit), "total": count_tickets(tenant)}


@app.post("/api/v1/admin/tickets/{ticket_id}/approval")
def admin_approve(ticket_id: str, payload: ApprovalRequest, request: Request):
    claims = admin_claims(request, csrf=True)
    try:
        return approve_ticket(ticket_id, claims["tenant_id"], payload.approved,
                              payload.operator, payload.comment)
    except LookupError as exc:
        message = str(exc)
        raise HTTPException(status_code=404 if message == "工单不存在" else 409, detail=message)


@app.get("/api/v1/admin/tickets/{ticket_id}/audit-logs")
def admin_audit(ticket_id: str, request: Request):
    claims = admin_claims(request)
    if not get_ticket(ticket_id, claims["tenant_id"]):
        raise HTTPException(status_code=404, detail="工单不存在")
    return list_audit_logs(ticket_id, claims["tenant_id"])


@app.get("/api/v1/admin/knowledge")
def admin_knowledge(request: Request):
    claims = admin_claims(request)
    return list_documents(claims["tenant_id"])


@app.post("/api/v1/admin/knowledge", status_code=201)
async def upload_knowledge(request: Request, file: UploadFile = File(...)):
    claims = admin_claims(request, csrf=True)
    raw = await file.read(5 * 1024 * 1024 + 1)
    try:
        fmt, content = extract_text(file.filename or "knowledge.txt", raw)
        return import_document(claims["tenant_id"], Path(file.filename or "knowledge").stem,
                               fmt, content, with_embedding=settings.embedding_provider != "disabled")
    except (ValueError, RuntimeError, ImportError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# 老路径仅保留提交兼容，查询和审批必须使用受保护的新接口，避免成为权限绕过入口。
@app.post("/工单", response_model=TicketResponse, status_code=201, include_in_schema=False)
def legacy_create(payload: TicketRequest, request: Request):
    return create_ticket(payload, request)


@app.post("/tickets", response_model=TicketResponse, status_code=201, include_in_schema=False)
def legacy_create_en(payload: TicketRequest, request: Request):
    return create_ticket(payload, request)
