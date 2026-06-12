from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

def _split_keywords(value:str) -> list[str]:
    return[item.strip() for item in value.split(",")if item.strip()]

@dataclass(frozen=True)
class Settings:
    app_name:str=os.getenv("APP_NAME","企业工单智能处理 Agent 系统")
    app_env:str=os.getenv("APP_ENV","dev")
    database_url:str=os.getenv("DATABASE_URL","data/tickets.db")
    llm_api_key:str=os.getenv("LLM_API_KEY",os.getenv("OPENAI_API_KEY",""))
    llm_base_url:str=os.getenv("LLM_BASE_URL",os.getenv("OPENAI_BASE_URL",""))
    llm_model:str=os.getenv("LLM_MODEL",os.getenv("OPENAI_MODEL","gpt-5-mini"))
    openai_api_key:str=os.getenv("OPENAI_API_KEY","")
    openai_base_url:str=os.getenv("OPENAI_BASE_URL","")
    open_model:str=os.getenv("OPEN_MODEL","gpt-5-mini")
    auto_approve_low_risk:bool=os.getenv("AUTO_APPROVE_LOW_RISK","false").lower()=="true"
    high_risk_keywords:tuple[str,...]=tuple(
        _split_keywords(
            os.getenv(
                "HIGH_RISK_KEYWORDS",
                "生产,支付,订单,财务,法务,高管,管理员,批量,数据删除,权限提升,海外访问",
            )
        )
    )

    @property
    def database_path(self) -> Path:
        return Path(self.database_url)

settings=Settings()