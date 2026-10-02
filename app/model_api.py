"""Versioned model connections; keys never leave the server or follow a changed URL."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import sqlite3
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
import httpx
from openai import OpenAI, OpenAIError, AuthenticationError, RateLimitError, APITimeoutError
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.config import settings
from app.database import get_connection


PROVIDERS = {
    'mimo': {'label': '小米 MiMo', 'base_url': 'https://api.xiaomimimo.com/v1', 'model': 'mimo-v2.6-flash'},
    'deepseek': {'label': 'DeepSeek', 'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-chat'},
    'openai': {'label': 'OpenAI', 'base_url': 'https://api.openai.com/v1', 'model': 'gpt-4o-mini'},
    'siliconflow_cn': {'label': '硅基流动（国内版）', 'base_url': 'https://api.siliconflow.cn/v1', 'model': 'Qwen/Qwen3-8B'},
    'compatible': {'label': '其他 OpenAI 兼容服务', 'base_url': '', 'model': ''},
}


class ModelApiInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=0)
    provider: str
    base_url: str = Field(max_length=300)
    model: str = Field(min_length=1, max_length=120)
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(''), exclude=True)

    @field_validator('provider')
    @classmethod
    def known_provider(cls, value):
        if value not in PROVIDERS:
            raise ValueError('请选择支持的 API 类型')
        return value

    @field_validator('model')
    @classmethod
    def model_name(cls, value):
        value = value.strip()
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}', value):
            raise ValueError('请填写供应商提供的模型 ID，不能包含空格或控制字符')
        return value

    @field_validator('api_key')
    @classmethod
    def safe_key(cls, value):
        key = value.get_secret_value()
        if len(key) > 500 or (key and (key != key.strip() or any(ord(c) < 33 or ord(c) > 126 for c in key))):
            raise ValueError('API Key 格式不正确，请检查是否粘贴了空格或换行')
        return value


@dataclass(frozen=True)
class ModelConnection:
    provider: str
    base_url: str
    model: str
    api_key: str = field(repr=False)
    version: int = 0
    source: str = 'environment'

    @property
    def driver(self):
        if self.base_url == PROVIDERS['siliconflow_cn']['base_url']:
            return 'siliconflow'
        return 'mimo' if self.provider == 'mimo' else 'generic'

    @property
    def enabled(self):
        return self.provider != 'disabled' and bool(self.api_key)


def normalize_url(value: str) -> str:
    value = value.strip().rstrip('/')
    p = urlsplit(value)
    if (p.scheme != 'https' or not p.hostname or not re.fullmatch(r'[a-zA-Z0-9.-]+', p.hostname)
            or p.username or p.password or p.query or p.fragment or p.port not in (None, 443)
            or '%' in value or '\\' in value or any(c.isspace() for c in value)):
        raise ValueError('API 地址必须是允许的 HTTPS 服务地址')
    return value


def validate_endpoint(provider: str, value: str) -> str:
    value = normalize_url(value)
    if provider != 'compatible':
        allowed = {PROVIDERS[provider]['base_url']}
    else:
        allowed = {p['base_url'] for p in PROVIDERS.values() if p['base_url']}
        allowed.update(normalize_url(x) for x in settings.model_api_allowed_base_urls.split(',') if x.strip())
        # MiMo requires its own request parameters, so use the dedicated API type.
        allowed.discard(PROVIDERS['mimo']['base_url'])
    if value not in allowed:
        raise ValueError('该 API 地址未开放，请选择官方服务，或由维护者配置允许的兼容地址')
    return value


def environment_connection() -> ModelConnection:
    return ModelConnection(settings.active_llm_provider, settings.active_llm_base_url or 'https://api.openai.com/v1',
                           settings.active_llm_model, settings.active_llm_api_key)


def _cipher() -> Fernet:
    if len(settings.session_secret) < 32:
        raise ValueError('服务器尚未配置模型密钥加密，请联系维护者')
    key = hmac.new(settings.session_secret.encode(), b'atlas:model-api:encryption:v1', hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _decrypt(row, tenant_id) -> str:
    try:
        data = json.loads(_cipher().decrypt(row['key_ciphertext'].encode()))
        if data['scope'] != [tenant_id, row['provider'], row['base_url']]:
            raise ValueError()
        return data['key']
    except (InvalidToken, KeyError, ValueError, TypeError):
        raise ValueError('已保存的模型 Key 无法读取，请重新配置或恢复部署默认') from None


def _state(db, tenant_id):
    row = db.execute('SELECT * FROM model_api_settings WHERE tenant_id=?', (tenant_id,)).fetchone()
    return dict(row) if row else {'version': 0, 'active_provider': ''}


def resolve_connection(tenant_id='demo') -> ModelConnection:
    with get_connection() as db:
        try:
            state = _state(db, tenant_id)
        except sqlite3.OperationalError as exc:
            if 'no such table' not in str(exc):
                raise
            return environment_connection()
        if not state['active_provider']:
            default = environment_connection()
            return ModelConnection(default.provider, default.base_url, default.model, default.api_key, state['version'])
        row = db.execute('SELECT * FROM model_api_profiles WHERE tenant_id=? AND provider=?',
                         (tenant_id, state['active_provider'])).fetchone()
        if row is None:
            raise ValueError('模型配置不完整，请恢复部署默认')
        return ModelConnection(row['provider'], validate_endpoint(row['provider'], row['base_url']),
                               row['model'], _decrypt(row, tenant_id), state['version'], 'administrator')


def public_configuration(tenant_id):
    with get_connection() as db:
        state = _state(db, tenant_id)
        rows = db.execute('SELECT provider,base_url,model FROM model_api_profiles WHERE tenant_id=?', (tenant_id,)).fetchall()
    saved = {r['provider']: dict(r) for r in rows}
    default = environment_connection()
    profiles = []
    for provider, data in PROVIDERS.items():
        item = {'provider': provider, **data, 'key_configured': False}
        if provider in saved:
            item.update(saved[provider], key_configured=True)
        elif default.enabled and default.base_url == data['base_url']:
            item.update(model=default.model, key_configured=True)
        profiles.append(item)
    active = saved.get(state['active_provider']) if state['active_provider'] else None
    return {'version': state['version'], 'source': 'administrator' if active else 'environment',
            'active': {'provider': active['provider'] if active else default.provider,
                       'model': active['model'] if active else default.model,
                       'base_url': active['base_url'] if active else default.base_url},
            'profiles': profiles,
            'allowed_compatible_urls': sorted({p['base_url'] for p in PROVIDERS.values() if p['base_url'] and p is not PROVIDERS['mimo']}
                                               | {normalize_url(x) for x in settings.model_api_allowed_base_urls.split(',') if x.strip()})}


def _candidate(db, tenant_id, payload):
    state = _state(db, tenant_id)
    if state['version'] != payload.expected_version:
        raise LookupError('模型配置已被其他管理员更新，请重新加载后再操作')
    base_url = validate_endpoint(payload.provider, payload.base_url)
    row = db.execute('SELECT * FROM model_api_profiles WHERE tenant_id=? AND provider=?', (tenant_id, payload.provider)).fetchone()
    key = payload.api_key.get_secret_value()
    if not key and row and row['base_url'] == base_url:
        key = _decrypt(row, tenant_id)
    default = environment_connection()
    if not key and not row and payload.provider != 'compatible' and default.base_url == base_url:
        key = default.api_key
    if not key:
        raise ValueError('这个 API 尚未配置 Key；更换地址也必须填写新的 Key')
    return ModelConnection(payload.provider, base_url, payload.model, key, state['version'] + 1, 'administrator')


def save_configuration(tenant_id, operator, payload):
    with get_connection() as db:
        db.execute('BEGIN IMMEDIATE')
        connection = _candidate(db, tenant_id, payload)
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        # Saved credentials remain encrypted and bound to the selected provider and endpoint.
        ciphertext = _cipher().encrypt(json.dumps({'scope': [tenant_id, connection.provider, connection.base_url],
                                                   'key': connection.api_key}).encode()).decode()
        db.execute('''INSERT INTO model_api_profiles VALUES(?,?,?,?,?,?)
            ON CONFLICT(tenant_id,provider) DO UPDATE SET base_url=excluded.base_url,model=excluded.model,
                key_ciphertext=excluded.key_ciphertext,updated_at=excluded.updated_at''',
                   (tenant_id, connection.provider, connection.base_url, connection.model, ciphertext, now))
        _set_active(db, tenant_id, operator, connection.version, connection.provider, 'switch', connection.base_url, connection.model, now)
    return public_configuration(tenant_id)


def _set_active(db, tenant_id, operator, version, provider, action, base_url, model, now):
    db.execute('''INSERT INTO model_api_settings VALUES(?,?,?) ON CONFLICT(tenant_id)
        DO UPDATE SET version=excluded.version,active_provider=excluded.active_provider''', (tenant_id, version, provider))
    db.execute('INSERT INTO model_api_audit(tenant_id,version,action,provider,base_url,model,operator,created_at) VALUES(?,?,?,?,?,?,?,?)',
               (tenant_id, version, action, provider, base_url, model, operator, now))


def restore_default(tenant_id, operator, expected_version):
    with get_connection() as db:
        db.execute('BEGIN IMMEDIATE')
        state = _state(db, tenant_id)
        if state['version'] != expected_version:
            raise LookupError('模型配置已被其他管理员更新，请重新加载后再操作')
        default = environment_connection()
        _set_active(db, tenant_id, operator, state['version'] + 1, '', 'restore_environment', default.base_url,
                    default.model, datetime.now(timezone.utc).isoformat(timespec='seconds'))
    return public_configuration(tenant_id)


def client_arguments(connection, timeout=30.0):
    # Do not send credentials through ambient proxies or redirect them to another host.
    return {'api_key': connection.api_key, 'base_url': connection.base_url, 'timeout': timeout, 'max_retries': 0,
            'http_client': httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False)}


def create_client(connection, timeout=30.0):
    return OpenAI(**client_arguments(connection, timeout))


def generation_options(connection, output_limit):
    """Provider-specific output options shared by probes, drafts and reviews."""
    if connection.driver == 'mimo':
        return {'max_completion_tokens': output_limit, 'extra_body': {'thinking': {'type': 'disabled'}}}
    options = {'max_tokens': output_limit}
    if connection.driver == 'siliconflow' and connection.model == 'Qwen/Qwen3-8B':
        options['extra_body'] = {'enable_thinking': False}
    return options


def test_configuration(tenant_id, payload):
    from app.security import use_quota
    with get_connection() as db:
        connection = _candidate(db, tenant_id, payload)
    if not use_quota('model-api-test-hour', tenant_id, 10, 3600) or not use_quota('llm-day', tenant_id, settings.max_llm_daily, 86400):
        raise OverflowError('模型测试额度已用完，请稍后再试')
    try:
        options = generation_options(connection, 256)
        with create_client(connection, timeout=15.0) as client:
            response = client.chat.completions.create(model=connection.model,
                messages=[{'role': 'user', 'content': '这是连接测试。只返回 JSON：{"ok":true}，不要返回其他内容。'}],
                response_format={'type': 'json_object'}, **options)
        if json.loads(response.choices[0].message.content or '{}').get('ok') is not True:
            raise ValueError('接口可连接，但未返回要求的 JSON；请检查模型是否支持结构化输出')
    except AuthenticationError:
        raise ValueError('供应商拒绝了 API Key，请检查 Key 是否有效') from None
    except RateLimitError:
        raise ValueError('供应商限流或余额不足，请检查供应商控制台') from None
    except APITimeoutError:
        raise ValueError('连接测试超时，请稍后重试') from None
    except (OpenAIError, json.JSONDecodeError, AttributeError, IndexError, TypeError):
        raise ValueError('连接或 JSON 输出测试失败，请核对 API 类型、地址和模型 ID') from None
    return {'passed': True, 'message': '连接与 JSON 输出测试通过，尚未切换；点击保存并切换后生效。'}
