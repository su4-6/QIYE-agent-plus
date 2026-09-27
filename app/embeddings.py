from __future__ import annotations

import struct
from functools import lru_cache

from app.config import settings


@lru_cache(maxsize=1)
def _local_model():
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=settings.embedding_model, threads=2)


def model_id() -> str:
    return "gemini-embedding-2:768" if settings.embedding_provider == "gemini" else settings.embedding_model


def embed(text: str, *, document: bool = False) -> bytes | None:
    if settings.embedding_provider == "disabled":
        return None
    if settings.embedding_provider == "gemini":
        from google import genai
        from google.genai import types
        if not settings.llm_api_key:
            raise RuntimeError("Gemini 向量化缺少模型密钥")
        client = genai.Client(api_key=settings.llm_api_key)
        result = client.models.embed_content(
            model="gemini-embedding-2", contents=text,
            config=types.EmbedContentConfig(
                output_dimensionality=768,
                task_type="RETRIEVAL_DOCUMENT" if document else "RETRIEVAL_QUERY",
            ),
        )
        values = result.embeddings[0].values
    elif settings.embedding_provider == "local":
        values = next(iter(_local_model().embed([text])))
    else:
        raise ValueError("未知向量化服务")
    return struct.pack(f"{len(values)}f", *values)
