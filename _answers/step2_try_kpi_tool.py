"""2단계 확인용 — MCP 서버를 같은 프로세스 안에서 띄워 KPI 조회 도구를 직접 불러 본다."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # corewatch_agent/를 import 경로에 추가

from fastmcp import Client
from mcp_server import mcp


async def main():
    async with Client(mcp) as client:
        tools = await client.list_tools()
        print("도구 목록:", [t.name for t in tools])

        result = await client.call_tool("get_kpi_series", {
            "device": "MME071", "kpi": "pag_sms_succ_rate",
            "start": "2022-03-12 05:00", "end": "2022-03-12 10:00",
        })
        print(json.dumps(json.loads(result.content[0].text), ensure_ascii=False, indent=2))

        bad = await client.call_tool("get_kpi_series", {
            "device": "MME999", "kpi": "pag_sms_att", "start": "2022-03-12 05:00", "end": "2022-03-12 10:00",
        })
        print("잘못된 장비 →", json.loads(bad.content[0].text)["error"])


asyncio.run(main())
