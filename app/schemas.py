from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TicketRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    title: str = Field(..., alias="工单标题", min_length=3, max_length=120)
    description: str = Field(..., alias="工单描述", min_length=8, max_length=3000)
    requester: str = Field(default="演示访客", alias="提交人", min_length=1, max_length=50)
    turnstile_token: str = Field(default="", alias="人机验证令牌", exclude=True)

    @field_validator("title", "description", "requester")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    approved: bool = Field(..., alias="是否通过")
    operator: str = Field(default="IT 审批员", alias="审批人", min_length=2, max_length=50)
    comment: str = Field(default="", alias="审批意见", max_length=500)


class LoginRequest(BaseModel):
    password: str = Field(min_length=8, max_length=200)


class TicketResponse(BaseModel):
    ticket_id: str
    access_token: str | None = None
    title: str
    description: str
    requester: str
    category: str
    priority: str
    risk_level: str
    status: str
    answer: str
    answer_source: str
    needs_human_approval: bool
    confidence: float = Field(description="兼容字段，新记录等于 evidence_score；不是答案正确概率", deprecated=True)
    evidence_score: float | None = None
    handoff_reason: str = ""
    request_id: str = ""
    citations: list[dict]


class HealthResponse(BaseModel):
    status: str
    database: str
    retrieval: str
    llm_enabled: bool
    llm_provider: str
    llm_model: str
    vector: dict = Field(default_factory=dict)
