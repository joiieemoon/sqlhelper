"""Find the correct way to read rate-limit headers from groq-sdk 1.x."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=True)

from groq import AsyncGroq

KEY = os.getenv("GROQ_API_KEY", "")


async def main():
    client = AsyncGroq(api_key=KEY, max_retries=0, timeout=60)

    print("=== chat.completions attrs ===")
    print([a for a in dir(client.chat.completions) if not a.startswith("_")])

    if hasattr(client.chat.completions, "with_raw_response"):
        print("\n=== with_raw_response attrs ===")
        print([a for a in dir(client.chat.completions.with_raw_response)
               if not a.startswith("_")])

        print("\n=== calling with_raw_response.create ===")
        raw = await client.chat.completions.with_raw_response.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": "say ok"}],
            max_tokens=512,
        )
        print("type:", type(raw))
        print("has .headers:", hasattr(raw, "headers"))
        print("has .parse:", hasattr(raw, "parse"))
        print("has .http_response:", hasattr(raw, "http_response"))

        hdrs = getattr(raw, "headers", None)
        if hdrs is None:
            hr = getattr(raw, "http_response", None)
            hdrs = getattr(hr, "headers", None)

        print("\n--- rate limit headers ---")
        if hdrs:
            for k, v in dict(hdrs).items():
                if "ratelimit" in k.lower():
                    print(f"  {k} = {v}")
        else:
            print("  none found")


asyncio.run(main())