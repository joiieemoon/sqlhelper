import asyncio
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
for _sub in (BASE, os.path.join(BASE, "llm-20260922T070723Z-1-001")):
    if _sub not in sys.path:
        sys.path.insert(0, _sub)
from dotenv import load_dotenv
load_dotenv(os.path.join(BASE, ".env"), override=True)

from llm.llm import call_llm

async def test():
    # Simple test: ask for a JSON response
    rsp = await call_llm(
        "Return valid JSON: {\"hello\": \"world\"}",
        "You are a helpful assistant. Always respond with valid JSON only. No text outside JSON.",
    )
    print("RAW RESPONSE:")
    print(repr(rsp))
    print()
    print("LENGTH:", len(rsp))
    print()

    # Now test with a real SQL optimization prompt
    sql_prompt = """Find issues in this query:

Query: SELECT * FROM customers WHERE country='USA'

Schema: customers(id INT, name TEXT, country TEXT)

Scope: []
Custom Request: NONE"""

    system = """You are a SQL performance analyst. Respond with JSON: {"issues": [], "summary": ""}"""

    rsp2 = await call_llm(sql_prompt, system)
    print("SQL LLM RAW RESPONSE:")
    print(repr(rsp2))
    print()
    print("LENGTH:", len(rsp2))

asyncio.run(test())