"""3단계 확인용 — 이상 탐지 도구와 알람 목록 도구를 직접 불러 본다."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastmcp import Client
from mcp_server import mcp

async def main() -> None:
    async with Client(mcp) as client:
        print("도구 목록:", [t.name for t in await client.list_tools()])

        r = await client.call_tool("detect_anomaly", {
            "device": "MME071", "kpi": "pag_sms_succ_rate",
            "start": "2022-03-12 06:00", "end": "2022-03-12 10:00",
        })
        d = json.loads(r.content[0].text)
        print(f"\n[detect_anomaly] 모델={d['model']} 기준선={d['threshold']} 알람 수={d['alarm_count']}")
        for p in d["points"]:
            mark = "  ← 알람" if p["is_alarm"] else ""
            print(f"  {p['ts']}  값 {p['value']:>6}  평소 {p['usual']:>6}  점수 {p['score']}{mark}")

        r = await client.call_tool("list_alarms", {"start": "2022-03-12 07:00", "end": "2022-03-12 09:00", "limit": 5})
        print("\n[list_alarms] 03-12 07~09시 상위 5건")
        for a in json.loads(r.content[0].text):
            print(f"  {a['ts']}  {a['device']}  {a['kpi']:<22} {a['direction']}  값 {a['value']} (평소 {a['usual']})")


asyncio.run(main())