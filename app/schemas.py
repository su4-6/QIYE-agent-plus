from __future__ import annotations

from typing import Literal
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
    username: str = Field(default='admin', min_length=3, max_length=40)
    password: str = Field(min_length=8, max_length=200)
    turnstile_token: str = Field(default='', exclude=True)


class EmployeeLoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r'^[a-zA-Z0-9_.-]+$')
    password: str = Field(min_length=8, max_length=128)
    turnstile_token: str = Field(default='', exclude=True)


class EmployeeRegisterRequest(EmployeeLoginRequest):
    display_name: str = Field(min_length=1, max_length=50)

    @field_validator('display_name')
    @classmethod
    def display_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('请填写称呼')
        return value.strip()


class TicketMessageRequest(BaseModel):
    body: str = Field(min_length=1, max_length=2000)

    @field_validator("body")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("消息不能为空")
        return value.strip()


class EmployeeActionRequest(BaseModel):
    action: Literal["resolve", "escalate", "reopen", "retry_ai"]
    comment: str = Field(default="", max_length=2000)
    expected_version: int = Field(ge=0)


class AdminWorkRequest(BaseModel):
    action: Literal["start", "reply", "request_info", "resolve"]
    body: str = Field(default="", max_length=2000)
    expected_version: int = Field(ge=0)


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
    public_answer: str = ""
    assigned_to: str = ""
    workflow_version: int = 0
    messages: list[dict] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    approval_passed: bool = False
    handoff_summary: str = ''


class HealthResponse(BaseModel):
    status: str
    database: str
    retrieval: str
    llm_enabled: bool
    llm_provider: str
    llm_model: str
    vector: dict = Field(default_factory=dict)
