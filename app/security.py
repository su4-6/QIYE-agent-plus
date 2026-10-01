from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException, Request

from app.config import settings
from app.database import get_connection


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return f"{salt.hex()}:{digest.hex()}"


def check_password(password: str) -> bool:
    try:
        salt_hex, expected = settings.admin_password_hash.split(":", 1)
        actual = password_hash(password, bytes.fromhex(salt_hex)).split(":", 1)[1]
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_session(tenant_id: str = "demo") -> tuple[str, str]:
    csrf = secrets.token_urlsafe(24)
    body = base64.urlsafe_b64encode(json.dumps({
        "tenant_id": tenant_id, "role": "admin", "username": settings.admin_username, "exp": int(time.time()) + 8 * 3600,
        "csrf": csrf,
    }, separators=(",", ":")).encode()).decode()
    signature = hmac.new(settings.session_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}", csrf


def admin_claims(request: Request, *, csrf: bool = False) -> dict:
    token = request.cookies.get("ticket_session", "")
    try:
        body, signature = token.rsplit(".", 1)
        expected = hmac.new(settings.session_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not settings.session_secret or not hmac.compare_digest(signature, expected):
            raise ValueError()
        claims = json.loads(base64.urlsafe_b64decode(body))
        if claims.get("role") != "admin" or claims.get("exp", 0) < time.time():
            raise ValueError()
        if csrf and not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), claims["csrf"]):
            raise ValueError()
        return claims
    except (ValueError, KeyError, json.JSONDecodeError):
        raise HTTPException(status_code=403, detail="需要管理员登录或有效的防伪令牌")


def new_access_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


def check_access_token(token: str, expected_hash: str) -> bool:
    return bool(token and expected_hash and hmac.compare_digest(
        hashlib.sha256(token.encode()).hexdigest(), expected_hash))


def use_quota(scope: str, key: str, limit: int, seconds: int) -> bool:
    current_time = int(time.time())
    bucket = str(current_time // seconds)
    identity = hashlib.sha256(key.encode()).hexdigest()[:24]
    with get_connection() as db:
        db.execute("DELETE FROM rate_limits WHERE expires_at <= ?", (current_time,))
        db.execute("""INSERT INTO rate_limits(scope,bucket,count,expires_at) VALUES(?,?,1,?)
            ON CONFLICT(scope,bucket) DO UPDATE SET count=count+1""",
            (scope + ":" + identity, bucket, (int(bucket)+1)*seconds))
        count = db.execute("SELECT count FROM rate_limits WHERE scope=? AND bucket=?",
                           (scope + ":" + identity, bucket)).fetchone()[0]
    return count <= limit


def verify_turnstile(token: str) -> None:
    if settings.app_env != "production":
        return
    if not token:
        raise HTTPException(status_code=403, detail="请完成人机验证")
    try:
        response = httpx.post("https://challenges.cloudflare.com/turnstile/v0/siteverify",
                              data={"secret": settings.turnstile_secret, "response": token}, timeout=5)
        payload = response.json()
        if not response.is_success or not payload.get("success"):
            raise HTTPException(status_code=403, detail="人机验证失败")
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="人机验证暂不可用")


def validate_production_config() -> None:
    if settings.app_env != "production":
        return
    required = (settings.session_secret, settings.admin_password_hash,
                settings.turnstile_secret, settings.turnstile_site_key)
    if not all(required) or len(settings.session_secret) < 32:
        raise RuntimeError("生产环境缺少会话、管理员或 Turnstile 配置")


if __name__ == "__main__":
    import getpass
    print(password_hash(getpass.getpass("管理员密码: ")))
