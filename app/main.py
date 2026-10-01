from __future__ import annotations

import logging
import time
import uuid
import hmac
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse

from app.agent import ticket_graph
from app.config import settings
from app.database import init_database, get_connection
from app.knowledge import extract_text, import_document, seed_demo
from app.llm import llm_status
from app.repository import (approve_ticket, count_tickets, get_ticket, get_ticket_with_secret,
                            list_audit_logs, list_documents, list_tickets, save_ticket, continue_assistance)
from app.schemas import (ApprovalRequest, HealthResponse, LoginRequest, TicketRequest, TicketResponse,
                         TicketMessageRequest, EmployeeActionRequest, AdminWorkRequest,
                         EmployeeLoginRequest, EmployeeRegisterRequest, ServicePolicyRequest)
from app.employees import employee_claims, authenticate, COOKIE
from app.workflow import change_ticket
from app.security import (admin_claims, check_access_token, check_password, create_session,
                          new_access_token, use_quota, validate_production_config, verify_turnstile)
from app.retrieval_health import vector_health, verify_local_vector_runtime
from app.metrics import retrieval_metrics
from app.observability import request_id, event, configure_logging
from app.evidence import policy

logger = logging.getLogger(__name__)
INDEX_PATH = Path(__file__).parent / "templates" / "index.html"
ADMIN_PATH = Path(__file__).parent / "templates" / "admin.html"
DEMO_TENANT = "demo"


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    validate_production_config()
    init_database()
    seed_demo()
    verify_local_vector_runtime()
    yield


app = FastAPI(title=settings.app_name, version="2.1.0", lifespan=lifespan,
              docs_url="/api/docs" if settings.app_env != "production" else None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    correlation = uuid.uuid4().hex
    token = request_id.set(correlation)
    started = time.perf_counter()
    try:
        response = await call_next(request)
        event("http_request", method=request.method, status=response.status_code,
              latency_ms=round((time.perf_counter()-started)*1000, 3))
    finally:
        request_id.reset(token)
    response.headers["X-Request-ID"] = correlation
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


def public_ticket(ticket_id: str, token: str, request: Request | None = None, *, csrf=False) -> dict:
    row = get_ticket_with_secret(ticket_id, DEMO_TENANT)
    if not row:
        raise HTTPException(status_code=404, detail="工单不存在或访问凭证无效")
    if row['employee_id']:
        claims = employee_claims(request, csrf=csrf, optional=True) if request else None
        if not claims or claims['employee_id'] != row['employee_id'] or claims['tenant_id'] != row['tenant_id']:
            raise HTTPException(status_code=404, detail="工单不存在或访问凭证无效")
    elif not check_access_token(token, row['access_token_hash']):
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


@app.get('/favicon.svg', include_in_schema=False)
def favicon():
    return FileResponse(INDEX_PATH.parent / 'favicon.svg', media_type='image/svg+xml',
                        headers={'Cache-Control': 'public, max-age=86400'})


@app.post('/api/v1/employee/register', status_code=201)
def employee_register(payload: EmployeeRegisterRequest, request: Request, response: Response):
    return authenticate(payload, request, response, register=True)


@app.post('/api/v1/employee/login')
def employee_login(payload: EmployeeLoginRequest, request: Request, response: Response):
    return authenticate(payload, request, response)


@app.get('/api/v1/employee/me')
def employee_me(request: Request):
    claims = employee_claims(request)
    return {'username': claims['username'], 'display_name': claims['display_name'], 'csrf_token': claims['csrf']}


@app.post('/api/v1/employee/logout')
def employee_logout(request: Request, response: Response):
    employee_claims(request, csrf=True)
    response.delete_cookie(COOKIE)
    return {'ok': True}


@app.get('/api/v1/employee/tickets')
def my_tickets(request: Request, skip: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
    claims = employee_claims(request)
    with get_connection() as db:
        args = (claims['tenant_id'], claims['employee_id'])
        total = db.execute('SELECT COUNT(*) FROM tickets WHERE tenant_id=? AND employee_id=?', args).fetchone()[0]
        rows = db.execute('SELECT ticket_id,title,status,created_at FROM tickets WHERE tenant_id=? AND employee_id=? ORDER BY created_at DESC,ticket_id DESC LIMIT ? OFFSET ?',
                          (*args, limit, skip)).fetchall()
    return {'items': [dict(row) for row in rows], 'total': total}


@app.get("/health", response_model=HealthResponse, include_in_schema=False)
def health():
    model = llm_status()
    try:
        with get_connection() as db:
            db.execute("SELECT COUNT(*) FROM knowledge_documents").fetchone()
    except Exception as exc:
        logger.warning("database_health_failed type=%s", type(exc).__name__)
        return JSONResponse(status_code=503, content={"status": "error", "database": "unavailable",
            "retrieval": "unavailable", "vector": {"state": "runtime_failed", "reason": "database_unavailable"},
            "llm_enabled": model["enabled"], "llm_provider": model["provider"], "llm_model": model["model"]})
    vector = vector_health()
    ready = vector["state"] == "ready"
    mode = policy().get("default_mode", "bm25")
    retrieval = ("bm25+vector" if mode == "hybrid" else mode) if ready else "bm25"
    return {"status": "ok" if ready or vector["state"] == "disabled" else "degraded",
            "database": "ok", "retrieval": retrieval, "vector": vector,
            "llm_enabled": model["enabled"], "llm_provider": model["provider"],
            "llm_model": model["model"]}


@app.get("/health/live", include_in_schema=False)
def live():
    return {"status": "alive"}


@app.get("/api/v1/public-config")
def public_config():
    from app.approvals import get_policy
    return {"turnstile_site_key": settings.turnstile_site_key if settings.app_env == "production" else "",
            "low_risk_assistance": settings.low_risk_assistance,
            'service_policy':get_policy(DEMO_TENANT)}


@app.get('/api/v1/admin/service-policy')
def read_service_policy(request: Request):
    from app.approvals import get_policy
    claims=admin_claims(request)
    return get_policy(claims['tenant_id'])


@app.put('/api/v1/admin/service-policy')
def update_service_policy(payload: ServicePolicyRequest, request: Request):
    from app.approvals import publish_policy
    claims=admin_claims(request,csrf=True)
    try:
        return publish_policy(claims['tenant_id'],payload.expected_version,
                              payload.model_dump(exclude={'expected_version'}),claims['username'])
    except LookupError as exc:raise HTTPException(status_code=409,detail=str(exc))


@app.post("/api/v1/tickets", response_model=TicketResponse, status_code=201)
def create_ticket(payload: TicketRequest, request: Request):
    verify_turnstile(payload.turnstile_token)
    client_ip = request.client.host if request.client else "unknown"
    if not use_quota("ticket-hour", client_ip, settings.max_public_hourly, 3600):
        raise HTTPException(status_code=429, detail="提交过于频繁，请稍后再试")
    state = payload.model_dump(exclude={"turnstile_token"})
    employee = employee_claims(request, csrf=True, optional=True)
    if payload.request_kind=='service' and not employee:
        raise HTTPException(status_code=401,detail='请先登录员工账号再提交服务申请')
    if employee:
        state['requester'] = employee['display_name']
    result = ticket_graph.invoke({**state, "tenant_id": DEMO_TENANT, "allow_llm": True})
    token, token_hash = new_access_token()
    stored = {**result, "tenant_id": DEMO_TENANT, "access_token_hash": token_hash}
    if employee:
        stored['employee_id'] = employee['employee_id']
    save_ticket(stored)
    response = get_ticket(result["ticket_id"], DEMO_TENANT)
    assert response is not None
    return {**response, "access_token": token}


@app.get("/api/v1/tickets/{ticket_id}", response_model=TicketResponse)
def read_ticket(ticket_id: str, request: Request, x_ticket_token: str = Header(default="")):
    return public_ticket(ticket_id, x_ticket_token, request)


def workflow_result(ticket_id, actor, action, body="", expected_version=None):
    try:
        return change_ticket(ticket_id, DEMO_TENANT, actor, action, body, expected_version)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404 if str(exc)=="工单不存在" else 409, detail=str(exc))


@app.post("/api/v1/tickets/{ticket_id}/messages", response_model=TicketResponse)
def employee_message(ticket_id: str, payload: TicketMessageRequest, request: Request, x_ticket_token: str = Header(default="")):
    public_ticket(ticket_id, x_ticket_token, request, csrf=True)
    if not use_quota("ticket-message-hour", ticket_id, 30, 3600):
        raise HTTPException(status_code=429, detail="补充过于频繁，请稍后再试")
    result=workflow_result(ticket_id,"employee","message",payload.body)
    if result['status']=='AI处理中':
        try:return continue_assistance(ticket_id,DEMO_TENANT)
        except LookupError as exc:raise HTTPException(status_code=409,detail=str(exc))
    return result


@app.post("/api/v1/tickets/{ticket_id}/actions", response_model=TicketResponse)
def employee_action(ticket_id: str, payload: EmployeeActionRequest, request: Request, x_ticket_token: str = Header(default="")):
    public_ticket(ticket_id, x_ticket_token, request, csrf=True)
    result=workflow_result(ticket_id,"employee",payload.action,payload.comment,payload.expected_version)
    if result['status']=='AI处理中':
        try:return continue_assistance(ticket_id,DEMO_TENANT)
        except LookupError as exc:raise HTTPException(status_code=409,detail=str(exc))
    return result


@app.get("/api/v1/admin/tickets/{ticket_id}")
def admin_ticket_detail(ticket_id: str, request: Request):
    claims=admin_claims(request)
    result=get_ticket(ticket_id,claims["tenant_id"])
    if not result: raise HTTPException(status_code=404,detail="工单不存在")
    return result


@app.post("/api/v1/admin/tickets/{ticket_id}/work")
def admin_ticket_work(ticket_id: str, payload: AdminWorkRequest, request: Request):
    claims=admin_claims(request,csrf=True)
    try:
        return change_ticket(ticket_id,claims["tenant_id"],"admin",payload.action,payload.body,payload.expected_version,
                             operator=claims.get('username',settings.admin_username))
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))
    except LookupError as exc:raise HTTPException(status_code=404 if str(exc)=="工单不存在" else 409,detail=str(exc))


@app.post("/api/v1/admin/login")
def admin_login(payload: LoginRequest, response: Response, request: Request):
    verify_turnstile(payload.turnstile_token)
    client_ip = request.client.host if request.client else "unknown"
    if not use_quota("admin-login-hour", client_ip, 10, 3600):
        raise HTTPException(status_code=429, detail="登录尝试过多，请稍后再试")
    password_ok = check_password(payload.password)
    if not hmac.compare_digest(payload.username, settings.admin_username) or not password_ok:
        raise HTTPException(status_code=401, detail="账号或密码不正确")
    session, csrf = create_session(DEMO_TENANT)
    response.set_cookie("ticket_session", session, httponly=True, secure=settings.app_env == "production",
                        samesite="strict", max_age=8 * 3600)
    return {"csrf_token": csrf, "expires_in": 8 * 3600, "username": settings.admin_username}


@app.get('/api/v1/admin/me')
def administrator_me(request: Request):
    claims=admin_claims(request)
    return {'username':claims.get('username',settings.admin_username),'csrf_token':claims['csrf'],
            'expires_in':max(0,int(claims['exp']-time.time()))}


@app.post("/api/v1/admin/logout")
def admin_logout(request: Request, response: Response):
    admin_claims(request, csrf=True)
    response.delete_cookie("ticket_session")
    return {"ok": True}


@app.get("/api/v1/admin/tickets")
def admin_tickets(request: Request, skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                  status: str = Query(default="",max_length=40), keyword: str = Query(default="",max_length=120)):
    claims = admin_claims(request)
    tenant = claims["tenant_id"]
    return {"items": list_tickets(tenant, skip, limit,status,keyword), "total": count_tickets(tenant,status,keyword)}


@app.post("/api/v1/admin/tickets/{ticket_id}/approval")
def admin_approve(ticket_id: str, payload: ApprovalRequest, request: Request):
    claims = admin_claims(request, csrf=True)
    try:
        return approve_ticket(ticket_id, claims["tenant_id"], payload.approved,
                              claims.get('username',settings.admin_username), payload.comment)
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


@app.get("/api/v1/admin/retrieval-metrics")
def admin_metrics(request: Request, days: int = Query(7)):
    claims = admin_claims(request)
    if days not in {1, 7, 30}:
        raise HTTPException(status_code=422, detail="统计窗口仅支持 1、7、30 天")
    return retrieval_metrics(claims["tenant_id"], days)


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
