from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ServiceRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    service_type: Literal['software_install','equipment_loan']
    item: str=Field(min_length=1,max_length=80)
    company_device: bool=False
    requires_privilege: bool=False
    loan_days: int=Field(default=1,ge=1,le=90)

    @field_validator('item')
    @classmethod
    def item_nonempty(cls,value):
        if not value.strip():raise ValueError('请填写申请的软件或设备')
        return value.strip()


class ServicePolicyRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_version: int=Field(ge=0)
    enabled: bool
    software: list[str]=Field(max_length=30)
    equipment: list[str]=Field(max_length=30)
    denied_items: list[str]=Field(max_length=30)
    max_loan_days: int=Field(ge=1,le=30)

    @field_validator('software','equipment','denied_items')
    @classmethod
    def policy_items(cls,values):
        if any(not v.strip() or len(v.strip())>80 for v in values):raise ValueError('每项规则需为1至80字')
        return list(dict.fromkeys(v.strip() for v in values))

    @model_validator(mode='after')
    def distinct_lists(self):
        allowed={v.casefold() for v in self.software+self.equipment}
        if allowed & {v.casefold() for v in self.denied_items}:raise ValueError('允许与禁用清单不能包含同一项目')
        return self


class TicketRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    title: str = Field(..., alias="工单标题", min_length=3, max_length=120)
    description: str = Field(..., alias="工单描述", min_length=8, max_length=3000)
    requester: str = Field(default="演示访客", alias="提交人", min_length=1, max_length=50)
    turnstile_token: str = Field(default="", alias="人机验证令牌", exclude=True)
    request_kind: Literal['incident','service']='incident'
    service_request: ServiceRequest | None=None

    @model_validator(mode='after')
    def service_fields(self):
        if (self.request_kind=='service') != (self.service_request is not None):
            raise ValueError('服务申请需填写申请类型和具体信息；报修不能携带服务申请字段')
        return self

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
    can_retry_ai: bool = False
    request_kind: Literal['incident','service']='incident'
    service_request: dict=Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str
    database: str
    retrieval: str
    llm_enabled: bool
    llm_provider: str
    llm_model: str
    vector: dict = Field(default_factory=dict)
