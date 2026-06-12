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
    if settings.auto_approve_low_risk and risk_level == "低风险":
        return False
    return risk_level in {"高风险", "中风险"}
#工单编号生成
def create_it_ticket() -> str:
    return f"IT-{uuid4().hex[:8].upper()}"