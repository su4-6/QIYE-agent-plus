from __future__ import annotations

import logging

from openai import OpenAI, OpenAIError

from app.config import settings
#创建当前模块的日志记录器。__name__ 在运行时是 "app.llm"，这样日志就知道是哪个模块输出的。
logger = logging.getLogger(__name__)
#检查大模型是否可用
def is_llm_enabled() -> bool:
    return bool(settings.llm_api_key.strip())
#生成建议
def generate_ticket_answer(
    *,
    title: str,
    description: str,
    category: str,
    priority: str,
    risk_level: str,
    needs_human_approval: bool,
    context: str,
) -> str | None:
    if not is_llm_enabled():
        return None
#timeout=15.0 是本次优化新增的——如果大模型 API 挂了或网络不通，15 秒后自动放弃，不会让工单接口一直卡着等。    
    client_kwargs: dict = {
        "api_key": settings.llm_api_key,
        "timeout": 15.0,  # 15 秒超时，防止 API 挂死卡住整个请求
    }
    if settings.llm_base_url:
        client_kwargs["base_url"] = settings.llm_base_url
    client = OpenAI(**client_kwargs) 
#根据是否需要审批，给出不同的提示词。
    approval_hint = (
        "该工单需要人工审批。请强调不能直接执行高风险动作，并给出审批前排查建议。"
        if needs_human_approval
        else "该工单无需人工审批。请给出一线工程师可以先执行的排查步骤。"
    )
#这是发给 AI 的"提示词"（Prompt）。用 f"""...""" 写的多行字符串，{变量名} 会被替换成实际值。.strip() 去掉首尾空白。
    prompt = f"""
你是企业 IT 服务台智能助手，请基于工单信息和知识库内容，用中文生成专业、简洁、可执行的处理建议。

要求：
1. 先总结工单判断结果。
2. 再给出 3 到 5 条处理建议。
3. 如果需要人工审批，明确说明等待审批，不能擅自执行高风险变更。
4. 不要编造知识库中没有的企业内部制度编号。

工单标题：{title}
工单描述：{description}
工单分类：{category}
优先级：{priority}
风险等级：{risk_level}
审批要求：{approval_hint}

知识库内容：
{context or "未检索到相关知识库内容"}
""".strip()
#捕获失败原因日志，崩了切换
    try:
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": "你是专业的企业 IT 服务台智能助手。"},
                {"role": "user", "content": prompt},
            ],
        )
    except OpenAIError as exc:
        logger.warning("大模型调用失败，已降级为本地规则和知识库兜底答复：%s", exc)
        return None   
    
    return (response.choices[0].message.content or "").strip()
