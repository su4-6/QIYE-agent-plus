from __future__ import annotations

from uuid import uuid4

from app.config import settings
from app.constants import (
    CATEGORY_RULES,
    DEFAULT_CATEGORY,
    HIGH_PRIORITY_WORDS,
    MEDIUM_PRIORITY_WORDS,
)
#分类函数
def classify_category(title: str, description: str) -> str:
    text = f"{title} {description}".lower()
    for category, keywords in CATEGORY_RULES.items():
        if any(keyword.lower() in text for keyword in keywords):
            return category
    return DEFAULT_CATEGORY
#优先级判断
def evaluate_priority(title: str, description: str) -> str:
    text = f"{title} {description}"
    if any(word in text for word in HIGH_PRIORITY_WORDS):
        return "高"
    if any(word in text for word in MEDIUM_PRIORITY_WORDS):
        return "中"
    return "低"
#风险评估
def evaluate_risk_level(title: str, description: str, priority: str) -> str:
    text = f"{title} {description}"
    if any(keyword in text for keyword in settings.high_risk_keywords):
        return "高风险"
    if priority == "中":
        return "中风险"
    return "低风险"
#审批判断
def decide_approval(risk_level: str) -> bool:
    """Whether a human pre-approval is needed; not a grant of system access."""
    return not (settings.auto_approve_low_risk and risk_level == "低风险")


def evaluate_assistance_risk(title: str, description: str) -> str:
    """Read-only support risk is independent of business urgency.

    Preserve evaluate_risk_level for the frozen formal evaluation protocol.
    """
    text=f'{title} {description}'
    if any(keyword in text for keyword in settings.high_risk_keywords):
        return '高风险'
    if any(word in text for word in ('权限申请','申请权限','写权限','只读权限','权限变更',
                                    '重置MFA','MFA重绑','解除封禁','关闭防火墙','关闭防护',
                                    '卸载驱动','修改注册表','删除系统文件','恢复数据')):
        return '中风险'
    return '低风险'


def can_retry_assistance(ticket, employee_messages=()) -> bool:
    if (not settings.low_risk_assistance or not settings.auto_approve_low_risk
            or ('request_kind' in ticket.keys() and ticket['request_kind']=='service')
            or ticket['status']!='待人工处理' or ticket['assigned_to']
            or ticket['handoff_reason'] in {'employee_request','support_requires_it','clarification_limit'}):
        return False
    text=ticket['description']+'\n'+'\n'.join(employee_messages)
    return evaluate_assistance_risk(ticket['title'],text)=='低风险'
#工单编号生成
def create_it_ticket() -> str:
    return f"IT-{uuid4().hex[:8].upper()}"
