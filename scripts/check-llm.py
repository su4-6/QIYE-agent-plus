from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm import generate_grounded_answer, llm_status
from app.config import settings
from app.database import init_database
from app.knowledge import import_document, retrieve
from evaluation.run import isolated_directory


def main() -> None:
    status = llm_status()
    print(json.dumps(status, ensure_ascii=False))
    if not status["enabled"]:
        raise SystemExit("LLM is disabled or no supported API key was found.")
    original_database = settings.database_url
    try:
        with isolated_directory() as directory:
            object.__setattr__(settings, "database_url", str(directory / "smoke.db"))
            init_database()
            import_document("demo", "演示VPN排查规程", "txt",
                "VPN登录失败时先确认账号未锁定，再核对系统时间；仍失败时收集错误码并联系服务台。",
                with_embedding=False)
            hits = retrieve("VPN登录失败", "demo", mode="bm25")["hits"][:1]
            expected = [hits[0]["id"]]
            answer, citations = generate_grounded_answer(
                title="演示 VPN 登录失败", description="测试账号登录 VPN 时提示密码错误。",
                category="账号与访问", risk_level="低风险", hits=hits,
                allow_llm=True, tenant_id="demo")
    finally:
        object.__setattr__(settings, "database_url", original_database)
    print(json.dumps({"answer_ok": bool(answer), "citation_ids": citations}, ensure_ascii=False))
    if not answer or citations != expected:
        raise SystemExit("LLM response did not pass citation validation.")


if __name__ == "__main__":
    main()
