#具体流程就四句话：先加载所有段落 → 调分词 → 调打分 → 过滤排序 → 返回前 3 个。
from __future__ import annotations
#导入
import re
from functools import lru_cache
from pathlib import Path
#导入constants文件
from app.constants import CATEGORY_TERMS, DOMAIN_TERMS, FAULT_TOKENS, RISK_SENSITIVE_TOKENS
#知识库
KB_PATH = Path(__file__).resolve().parent.parent / "data" / "knowledge_base.txt"
#加载知识库
@lru_cache(maxsize=1)
def load_knowledge_chunks() -> list[str]:
    if not KB_PATH.exists():
        return []
    text = KB_PATH.read_text(encoding="utf-8")
    return [chunk.strip() for chunk in text.split("\n\n") if chunk.strip()]
#文本处理
def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()
#把符号替换为空格
def _tokenize(text: str) -> dict[str, int]:
    normalized = re.sub(r"[，。、,.；;：:\n\r\t]", " ", text.lower())
    tokens: dict[str, int] = {}
#基础分词，保留两个字符以上
    for token in normalized.split():
        if len(token) >= 2:
            tokens[token] = max(tokens.get(token, 0), 2)
#领域词匹配
    text_lower = text.casefold()
    for keyword, weight in DOMAIN_TERMS.items():
        keyword_lower = keyword.casefold()
        if keyword_lower in text_lower:
            tokens[keyword_lower] = max(tokens.get(keyword_lower, 0), weight)
#分类关联
    for category, keywords in CATEGORY_TERMS.items():
        if category.casefold() in text_lower:
            for keyword in keywords:
                keyword_lower = keyword.casefold()
                tokens[keyword_lower] = max(tokens.get(keyword_lower, 0), 1)

    return tokens
#标题评分
def _split_title_and_body(chunk: str) -> tuple[str, str]:
    lines = chunk.splitlines()
    if not lines:
        return "", ""
    return lines[0], "\n".join(lines[1:])
#段落评分
def _score_chunk(chunk: str, query_tokens: dict[str, int]) -> int:
    title, body = _split_title_and_body(chunk)
    title_lower = _normalize(title)
    body_lower = _normalize(body)
    score = 0

    for token, weight in query_tokens.items():
        if token in title_lower:
            score += weight * 4    # 命中标题 → 权重 × 4
        if token in body_lower:
            score += weight * 2    # 命中正文 → 权重 × 2

    if "风险" in chunk and any(
        token in query_tokens
        for token in ("审批", "管理员", "高管", "财务", "法务", "数据导出", "数据修复")
    ):
        score += 6    # 包含风险相关内容 + 查询涉及敏感词 → 额外加 6 分

    if "处理流程" in chunk and any(
        token in query_tokens
        for token in ("无法", "失败", "不可用", "申请", "连接")
    ):
        score += 4    # 包含处理流程 + 查询涉及故障词 → 额外加 4 分

    return score
#把上面的函数串起来执行，检索入口，
def retrieve_context(query: str, top_k: int = 3, *, corpus: list[str] | None = None) -> list[str]:
    chunks = corpus if corpus is not None else load_knowledge_chunks()
    if not chunks or top_k <= 0:
        return []

    query_tokens = _tokenize(query)
    if not query_tokens:
        return chunks[:top_k]

    scored_chunks = [
        (_score_chunk(chunk, query_tokens), index, chunk)
        for index, chunk in enumerate(chunks)
    ]
    matched_chunks = [item for item in scored_chunks if item[0] > 0]
    if matched_chunks:
        best_score = max(score for score, _, _ in matched_chunks)
        min_score = max(6, int(best_score * 0.4))
        matched_chunks = [item for item in matched_chunks if item[0] >= min_score]
    ranked = sorted(matched_chunks or scored_chunks, key=lambda item: (-item[0], item[1]))
    return [chunk for _, _, chunk in ranked[:top_k]]
