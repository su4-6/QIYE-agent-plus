from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm import generate_grounded_answer, llm_status


def main() -> None:
    status = llm_status()
    print(json.dumps(status, ensure_ascii=False))
    if not status["enabled"]:
        raise SystemExit("LLM is disabled or no supported API key was found.")
    answer, citations = generate_grounded_answer(
        title="演示 VPN 登录失败",
        description="测试账号登录 VPN 时提示密码错误。",
        category="账号与访问",
        risk_level="低风险",
        hits=[{
            "id": 9001,
            "title": "演示 VPN 排查规程",
            "version": 1,
            "content": "先确认账号未锁定，再核对系统时间；仍失败时收集错误码并联系服务台。",
        }],
        allow_llm=True,
    )
    print(json.dumps({"answer_ok": bool(answer), "citation_ids": citations}, ensure_ascii=False))
    if not answer or citations != [9001]:
        raise SystemExit("LLM response did not pass citation validation.")


if __name__ == "__main__":
    main()
