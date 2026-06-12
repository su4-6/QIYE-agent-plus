from pydantic import BaseModel, ConfigDict, Field
#提交工单
class TicketRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
       
    title: str = Field(..., alias="工单标题", description="工单标题", examples=["生产系统支付失败"])

    description: str = Field(
        ...,
        alias="工单描述",
        description="工单详细描述",
        examples=["线上订单支付大面积失败，需要立刻排查数据库和支付接口"],
    )
    requester: str = Field(default="新员工", alias="提交人", description="提交人")
#审批请求
class ApprovalRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    approved: bool = Field(..., alias="是否通过", description="是否审批通过")
    operator: str = Field(default="IT 审批员", alias="审批人", description="审批人")
    comment: str = Field(default="", alias="审批意见", description="审批意见")
#工单结果
class TicketResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ticket_id: str = Field(alias="工单编号")
    title: str = Field(alias="工单标题")
    description: str = Field(alias="工单描述")
    requester: str = Field(alias="提交人")
    category: str = Field(alias="工单分类")
    priority: str = Field(alias="优先级")
    risk_level: str = Field(alias="风险等级")
    status: str = Field(alias="当前状态")
    answer: str = Field(alias="处理建议")
    answer_source: str = Field(default="本地知识库兜底", alias="回答来源")
    needs_human_approval: bool = Field(alias="是否需要人工审批")
    retrieved_context: list[str] = Field(alias="命中的知识库内容")
#列表响应
#工单数据格式
class TicketListItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ticket_id: str = Field(alias="工单编号")
    title: str = Field(alias="工单标题")
    requester: str = Field(alias="提交人")
    category: str = Field(alias="工单分类")
    priority: str = Field(alias="优先级")
    risk_level: str = Field(alias="风险等级")
    status: str = Field(alias="当前状态")
    answer_source: str = Field(default="本地知识库兜底", alias="回答来源")
    needs_human_approval: bool = Field(alias="是否需要人工审批")
    created_at: str = Field(alias="创建时间")
#审计日志数据格式
class AuditLogItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(alias="序号")
    ticket_id: str = Field(alias="工单编号")
    action: str = Field(alias="操作")
    operator: str = Field(alias="操作人")
    detail: str = Field(alias="详情")
    created_at: str = Field(alias="创建时间")