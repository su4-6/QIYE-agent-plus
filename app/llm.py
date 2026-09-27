from __future__ import annotations

import json
import logging

from openai import OpenAI, OpenAIError

from app.config import settings

logger = logging.getLogger(__name__)


def is_llm_enabled() -> bool:
    return bool(settings.llm_api_key.strip())


def generate_grounded_answer(*, title: str, description: str, category: str,
                             risk_level: str, hits: list[dict], allow_llm: bool) -> tuple[str | None, list[int]]:
    if not allow_llm or not is_llm_enabled() or not hits:
        return None, []
    evidence = "\n\n".join(
        f"[资料 {hit['id']}] {hit['title']} v{hit['version']}\n{hit['content']}" for hit in hits[:4]
    )
    prompt = f"""你是企业 IT 服务台助手。只能根据给定资料回答，不得补充资料外的企业制度或已执行动作。
输出严格 JSON：{{"answer":"中文处理方案","citation_ids":[资料整数ID]}}。
答案包含判断和 3-5 条可执行排查建议；涉及高风险时只给审批前的只读检查建议。

工单标题：{title}
工单描述：{description}
分类：{category}
风险：{risk_level}

资料：
{evidence}"""
    client_args = {"api_key": settings.llm_api_key, "timeout": 15.0}
    if settings.llm_base_url:
        client_args["base_url"] = settings.llm_base_url
    try:
        response = OpenAI(**client_args).chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "system", "content": "只输出合法 JSON。"},
                      {"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content or "{}")
        valid = {hit["id"] for hit in hits}
        cited = [int(value) for value in data.get("citation_ids", []) if int(value) in valid]
        answer_text = str(data.get("answer", "")).strip()
        return (answer_text, cited) if answer_text and cited else (None, [])
    except (OpenAIError, json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.warning("模型生成失败，转为有来源的规则答复：%s", exc)
        return None, []
