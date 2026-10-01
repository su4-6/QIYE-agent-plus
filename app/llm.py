from __future__ import annotations

import json
import logging
import contextvars
import time

from openai import OpenAI, OpenAIError

from app.config import settings
from app.database import get_connection

logger = logging.getLogger(__name__)
last_generation = contextvars.ContextVar("last_generation", default={})


def validate_citations(cited, hits: list[dict], tenant_id: str | None = None) -> list[int]:
    if not isinstance(cited, list) or not cited or any(type(v) is not int for v in cited):
        raise ValueError("invalid_citation_shape")
    valid = {h["id"]: h for h in hits[:4]}
    tenants = {h["tenant_id"] for h in hits[:4]}
    if len(tenants) != 1 or (tenant_id is not None and tenants != {tenant_id}):
        raise ValueError("mixed_or_wrong_tenant_evidence")
    if any(v not in valid for v in cited):
        raise ValueError("citation_not_retrieved")
    with get_connection() as db:
        for value in cited:
            h = valid[value]
            row = db.execute("""SELECT c.document_id,c.tenant_id,d.version FROM knowledge_chunks c
                JOIN knowledge_documents d ON d.id=c.document_id
                WHERE c.id=? AND c.active=1 AND d.active=1""", (value,)).fetchone()
            if not row or (row["document_id"], row["tenant_id"], row["version"]) != (
                    h["document_id"], h["tenant_id"], h["version"]):
                raise ValueError("citation_scope_or_version_invalid")
    return list(dict.fromkeys(cited))


def is_llm_enabled() -> bool:
    return settings.active_llm_provider != "disabled"


def llm_status() -> dict[str, str | bool]:
    return {
        "enabled": is_llm_enabled(),
        "provider": settings.active_llm_provider,
        "model": settings.active_llm_model if is_llm_enabled() else "",
    }


def generate_grounded_answer(*, title: str, description: str, category: str,
                             risk_level: str, hits: list[dict], allow_llm: bool,
                             tenant_id: str | None = None) -> tuple[str | None, list[int]]:
    last_generation.set({"called": False})
    if not allow_llm or not is_llm_enabled() or not hits:
        return None, []
    try:
        # Reject invalid tenant/version context before it leaves this service.
        validate_citations([h["id"] for h in hits[:4]], hits, tenant_id)
    except Exception as exc:
        last_generation.set({"called": False, "error_type": type(exc).__name__})
        logger.warning("generation_context_rejected error_type=%s", type(exc).__name__)
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
    client_args = {"api_key": settings.active_llm_api_key, "timeout": 30.0, "max_retries": 0}
    if settings.active_llm_base_url:
        client_args["base_url"] = settings.active_llm_base_url
    try:
        started = time.perf_counter()
        last_generation.set({"called": True, "structured": False, "citations_valid": False})
        output_options = ({"max_completion_tokens": 1000, "extra_body": {"thinking": {"type": "disabled"}}}
                          if settings.active_llm_provider == "mimo" else {"max_tokens": 1000})
        response = OpenAI(**client_args).chat.completions.create(
            model=settings.active_llm_model,
            messages=[{"role": "system", "content": "只输出合法 JSON。"},
                      {"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            **output_options,
        )
        last_generation.set({"called": True, "latency_ms": (time.perf_counter()-started)*1000,
            "structured": False, "citations_valid": False,
            "usage": response.usage.model_dump() if response.usage else {},
            "finish_reason": response.choices[0].finish_reason,
            "raw_output": response.choices[0].message.content or ""})
        data = json.loads(response.choices[0].message.content or "{}")
        if not isinstance(data, dict) or not isinstance(data.get("answer"), str):
            raise ValueError("invalid_answer_shape")
        last_generation.set({**last_generation.get(), "structured": True})
        cited = validate_citations(data.get("citation_ids"), hits, tenant_id)
        last_generation.set({**last_generation.get(), "citations_valid": True})
        answer_text = data["answer"].strip()
        return (answer_text, cited) if answer_text and cited else (None, [])
    except (OpenAIError, json.JSONDecodeError, ValueError, TypeError) as exc:
        last_generation.set({**last_generation.get(), "error_type": type(exc).__name__})
        logger.warning("generation_failed error_type=%s", type(exc).__name__)
        return None, []
