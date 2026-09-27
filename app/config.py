from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "企业工单智能处理 Agent 系统")
    app_env: str = os.getenv("APP_ENV", "dev")
    database_url: str = os.getenv("DATABASE_URL", "data/tickets.db")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")
    llm_model: str = os.getenv("LLM_MODEL", "gemini-2.5-flash")
    embedding_provider: str = os.getenv("EMBEDDING_PROVIDER", "local")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    session_secret: str = os.getenv("SESSION_SECRET", "")
    admin_password_hash: str = os.getenv("ADMIN_PASSWORD_HASH", "")
    turnstile_secret: str = os.getenv("TURNSTILE_SECRET", "")
    turnstile_site_key: str = os.getenv("TURNSTILE_SITE_KEY", "")
    max_llm_daily: int = int(os.getenv("MAX_LLM_DAILY", "100"))
    max_public_hourly: int = int(os.getenv("MAX_PUBLIC_HOURLY", "10"))
    high_risk_keywords: tuple[str, ...] = tuple(
        word.strip() for word in os.getenv(
            "HIGH_RISK_KEYWORDS", "生产,支付,订单,财务,法务,高管,管理员,批量,数据删除,权限提升,海外访问"
        ).split(",") if word.strip()
    )

    @property
    def database_path(self) -> Path:
        return Path(self.database_url)


settings = Settings()
