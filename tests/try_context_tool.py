"""4단계 확인용 — 맥락 조회 도구를 직접 불러 본다."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastmcp import Client
from mcp_server import mcp

async def ask(client, device, kpi, ts):
    r = await client.call_tool("get_context", {"device": device, "kpi": kpi, "ts": ts})
    print(f"\n=== {device} · {kpi} · {ts} ===")
    print(json.dumps(json.loads(r.content[0].text), ensure_ascii=False, indent=2))

async def main():
    async with Client(mcp) as client:
        await ask(client, "MME071", "pag_sms_succ_rate", "2022-03-12 08:00")   # 장비 9대 동시 이상
        await ask(client, "MME093", "pag_video_succ_rate", "2022-03-12 07:00")  # 장비 1대만 이상


asyncio.run(main())