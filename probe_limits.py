"""
Probe the real token cost + rate-limit headers for each pipeline stage.

Captures the exact prompts that issue_finder / planner / rewriter send,
then fires ONE raw request per stage at Groq so we can read
x-ratelimit-remaining-tokens / x-ratelimit-reset-tokens / retry-after.
"""
import asyncio
import json
import os
import sys

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BACKEND, "llm-20260922T070723Z-1-001"))
sys.path.insert(0, os.path.join(BACKEND, "utils-20260922T070723Z-1-001"))

from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND, ".env"))

import httpx

API_KEY = os.getenv("GROQ_API_KEY")
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
URL = "https://api.groq.com/openai/v1/chat/completions"

# ── Capture the prompts the real stages build ────────────────────────
captured = {}


async def fake_call_llm(prompt, system_prompt):
    captured["prompt"] = prompt
    captured["system_prompt"] = system_prompt
    return '{"issues": []}'


import llm.issue_finder as ifm
import llm.planner as plm
import llm.rewriter as rwm

ifm.call_llm = fake_call_llm
plm.call_llm = fake_call_llm
rwm.call_llm = fake_call_llm

QUERY = "SELECT * FROM customers WHERE country='USA'"
SCHEMA = {
    "customers": {
        "row_count": 100000,
        "avg_row_length": 200,
        "columns": [
            {"name": "id", "type": "INT", "nullable": False},
            {"name": "name", "type": "VARCHAR(100)", "nullable": False},
            {"name": "country", "type": "VARCHAR(50)", "nullable": False},
        ],
        "indexes": [{"name": "PRIMARY", "cols": "id", "unique": True, "cardinality": 100000}],
    }
}
SCOPE = ["indexes"]
ISSUES = [{"id": "i1", "scope": "indexes", "title": "Full table scan", "location": "customers",
           "impact": "HIGH", "execution_time_cost": "ROW_WASTE", "root_cause": "no index on country",
           "evidence": "type=ALL"}]
PLAN = [{"id": "a1", "issue_id": "i1", "type": "CREATE_INDEX", "priority": 1,
         "before": "", "after": "", "table": "customers", "columns": ["country"],
         "ddl": "CREATE INDEX idx_country ON customers(country)", "reason": "avoid full scan"}]


async def capture_all():
    await ifm.issue_finder_llm(QUERY, {}, {"innodb_stats": {}}, SCOPE, SCHEMA, "")
    captured["if"] = (captured.pop("prompt"), captured.pop("system_prompt"))
    await plm.planner_llm(QUERY, ISSUES, SCHEMA, SCOPE, "")
    captured["pl"] = (captured.pop("prompt"), captured.pop("system_prompt"))
    await rwm.rewriter_llm(QUERY, SCHEMA, PLAN, "", SCOPE, ["a1"])
    captured["rw"] = (captured.pop("prompt"), captured.pop("system_prompt"))


async def send_raw(system_prompt, user_prompt):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(URL, headers={"Authorization": f"Bearer {API_KEY}"}, json=payload)
        return r


async def main():
    await capture_all()

    print("=" * 78)
    print("PROMPT SIZE (chars) -> estimated tokens (chars/4)")
    print("=" * 78)
    for k, label in (("if", "issue_finder"), ("pl", "planner"), ("rw", "rewriter")):
        up, sp = captured[k]
        total = len(up) + len(sp)
        print(f"{label:14s} system={len(sp):>7,}  user={len(up):>6,}  total={total:>7,}  ~tok={total // 4:>6,}")

    print()
    print("=" * 78)
    print("LIVE REQUEST PROBE (one per stage, sequential)")
    print("=" * 78)
    for k, label in (("if", "issue_finder"), ("pl", "planner"), ("rw", "rewriter")):
        up, sp = captured[k]
        r = await send_raw(sp, up)
        hdr = {h: v for h, v in r.headers.items() if "ratelimit" in h.lower() or h.lower() == "retry-after"}
        print(f"\n--- {label} ---")
        print(f"status: {r.status_code}")
        for hh, vv in sorted(hdr.items()):
            print(f"  {hh}: {vv}")
        if r.status_code == 200:
            body = r.json()
            print(f"  usage: {body.get('usage')}")
        else:
            print(f"  body: {r.text[:400]}")


asyncio.run(main())