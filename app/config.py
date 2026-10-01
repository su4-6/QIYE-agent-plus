from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Atlas Desk · 智能工单处理 Agent（个人项目）")
    app_env: str = os.getenv("APP_ENV", "dev")
    database_url: str = os.getenv("DATABASE_URL", "data/tickets.db")
    llm_provider: str = os.getenv("LLM_PROVIDER", "auto").strip().lower()
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")
    llm_model: str = os.getenv("LLM_MODEL", "gemini-2.5-flash")
    mimo_api_key: str = os.getenv("MIMO_API_KEY", "")
    mimo_base_url: str = os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1")
    mimo_model: str = os.getenv("MIMO_MODEL", "mimo-v2.5-pro")
    embedding_provider: str = os.getenv("EMBEDDING_PROVIDER", "local")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    session_secret: str = os.getenv("SESSION_SECRET", "")
    admin_password_hash: str = os.getenv("ADMIN_PASSWORD_HASH", "")
    turnstile_secret: str = os.getenv("TURNSTILE_SECRET", "")
    turnstile_site_key: str = os.getenv("TURNSTILE_SITE_KEY", "")
    max_llm_daily: int = int(os.getenv("MAX_LLM_DAILY", "100"))
    max_public_hourly: int = int(os.getenv("MAX_PUBLIC_HOURLY", "10"))
    low_risk_assistance: bool = os.getenv('LOW_RISK_ASSISTANCE', 'false').lower() == 'true'
    auto_approve_low_risk: bool = os.getenv('AUTO_APPROVE_LOW_RISK', 'true').lower() == 'true'
    admin_username: str = os.getenv('ADMIN_USERNAME', 'admin').strip()
    model_api_allowed_base_urls: str = os.getenv('MODEL_API_ALLOWED_BASE_URLS', '')
    high_risk_keywords: tuple[str, ...] = tuple(
        word.strip() for word in os.getenv(
            "HIGH_RISK_KEYWORDS", "生产,支付,订单,财务,法务,高管,管理员,批量,数据删除,权限提升,海外访问"
        ).split(",") if word.strip()
    )

    @property
    def database_path(self) -> Path:
        return Path(self.database_url)

    @property
    def active_llm_provider(self) -> str:
        if self.llm_provider == "disabled":
            return "disabled"
        if self.llm_provider == "mimo" or (self.llm_provider == "auto" and self.mimo_api_key.strip()):
            return "mimo" if self.mimo_api_key.strip() else "disabled"
        return "generic" if self.llm_api_key.strip() else "disabled"

    @property
    def active_llm_api_key(self) -> str:
        return self.mimo_api_key if self.active_llm_provider == "mimo" else self.llm_api_key

    @property
    def active_llm_base_url(self) -> str:
        return self.mimo_base_url if self.active_llm_provider == "mimo" else self.llm_base_url

    @property
    def active_llm_model(self) -> str:
        return self.mimo_model if self.active_llm_provider == "mimo" else self.llm_model


settings = Settings()
