"""Verify the groq SDK raw-response API (headers + parse) with a tiny call."""
import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from groq import AsyncGroq

client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))


async def main():
    wrapper = client.chat.completions.with_raw_response
    print("wrapper methods:", [m for m in dir(wrapper) if not m.startswith("_")])

    raw = await wrapper.create(
        model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
        messages=[{"role": "user", "content": "Reply with JSON: {\"ok\": true}"}],
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    print("raw type:", type(raw).__name__)
    print("has headers:", hasattr(raw, "headers"))
    print("has parse:", hasattr(raw, "parse"))
    hdrs = {h: v for h, v in raw.headers.items() if "ratelimit" in h.lower()}
    print("ratelimit headers:", hdrs)

    completion = raw.parse()
    print("content:", completion.choices[0].message.content)
    print("usage:", completion.usage)


asyncio.run(main())