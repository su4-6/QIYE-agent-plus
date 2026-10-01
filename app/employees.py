"""Employee portal identity using the existing SQLite and signed-session stack."""
import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import time

from fastapi import HTTPException, Request, Response

from app.config import settings
from app.database import get_connection
from app.repository import utc_now
from app.security import password_hash, use_quota, verify_turnstile

COOKIE = 'atlas_employee'


def employee_claims(request: Request, *, csrf=False, optional=False):
    token = request.cookies.get(COOKIE, '')
    if not token and optional:
        return None
    try:
        body, signature = token.rsplit('.', 1)
        expected = hmac.new(settings.session_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not settings.session_secret or not hmac.compare_digest(signature, expected):
            raise ValueError()
        claims = json.loads(base64.urlsafe_b64decode(body))
        if claims.get('role') != 'employee' or claims.get('exp', 0) <= time.time():
            raise ValueError()
        with get_connection() as db:
            account = db.execute('SELECT id,tenant_id,username,display_name FROM employee_accounts WHERE id=? AND tenant_id=?',
                                 (claims['employee_id'], claims['tenant_id'])).fetchone()
        if not account:
            raise ValueError()
        if csrf and not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), claims['csrf']):
            raise HTTPException(status_code=403, detail='操作验证失效，请刷新页面')
        return {**claims, **dict(account)}
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=401, detail='请先登录员工账号')


def session_response(account, response: Response):
    csrf = secrets.token_urlsafe(24)
    claims = {'role': 'employee', 'employee_id': account['id'], 'tenant_id': account['tenant_id'],
              'csrf': csrf, 'exp': int(time.time()) + 8 * 3600}
    body = base64.urlsafe_b64encode(json.dumps(claims, separators=(',', ':')).encode()).decode()
    signature = hmac.new(settings.session_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    response.set_cookie(COOKIE, body + '.' + signature, httponly=True, secure=settings.app_env == 'production',
                        samesite='strict', max_age=8 * 3600)
    return {'username': account['username'], 'display_name': account['display_name'], 'csrf_token': csrf}


def authenticate(payload, request, response, *, register=False):
    verify_turnstile(payload.turnstile_token)
    ip = request.client.host if request.client else 'unknown'
    if not use_quota('employee-register-hour' if register else 'employee-login-hour', ip, 5 if register else 12, 3600):
        raise HTTPException(status_code=429, detail='操作过于频繁，请稍后重试')
    username = payload.username.lower()
    with get_connection() as db:
        if register:
            account = {'id': secrets.token_hex(16), 'tenant_id': 'demo', 'username': username,
                       'display_name': payload.display_name}
            try:
                db.execute('INSERT INTO employee_accounts(id,tenant_id,username,display_name,password_hash,created_at) VALUES(?,?,?,?,?,?)',
                           (account['id'], 'demo', username, payload.display_name, password_hash(payload.password), utc_now()))
            except sqlite3.IntegrityError:
                raise HTTPException(status_code=409, detail='账号已存在，请登录或换一个账号名')
        else:
            row = db.execute('SELECT * FROM employee_accounts WHERE tenant_id=? AND username=?', ('demo', username)).fetchone()
            stored = row['password_hash'] if row else password_hash('nonexistent-account')
            salt, digest = stored.split(':', 1)
            actual = password_hash(payload.password, bytes.fromhex(salt)).split(':', 1)[1]
            if not hmac.compare_digest(actual, digest) or not row:
                raise HTTPException(status_code=401, detail='账号或密码不正确')
            account = dict(row)
    return session_response(account, response)
