import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=True)

from groq import AsyncGroq

KEY = os.getenv("GROQ_API_KEY", "")

# Models listed on this key that are genuine chat/completion models
LISTED_CHAT = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "allam-2-7b",
]

# Well-known Groq chat models that do NOT appear in this key's /models list.
# Probed anyway — /models can be an incomplete view, and a working model with a
# higher TPM ceiling would be worth switching to.
UNLISTED_PROBE = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "moonshotai/kimi-k2-instruct",
    "qwen/qwen3-32b",
    "gemma2-9b-it",
    "deepseek-r1-distill-llama-70b",
]


async def get_limits(model_id):
    """Return (headers_dict, error_string)."""
    client = AsyncGroq(api_key=KEY, max_retries=0, timeout=60)
    try:
        raw = await client.chat.completions.with_raw_response.create(
            model=model_id,
            messages=[{"role": "user", "content": "say ok"}],
            max_tokens=512,
        )
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:150]}"

    hdrs = {}
    try:
        h = raw.headers
        for k, v in dict(h).items():
            if "ratelimit" in k.lower() or k.lower() == "retry-after":
                hdrs[k] = v
    except Exception as e:
        hdrs["_err"] = str(e)
    return hdrs, None


async def main():
    print("=" * 78)
    print("MODELS LISTED ON THIS KEY")
    print("=" * 78)
    for m in LISTED_CHAT:
        h, err = await get_limits(m)
        print(f"\n{m}")
        if err:
            print(f"   FAIL -> {err}")
            continue
        print(f"   TPM limit     : {h.get('x-ratelimit-limit-tokens', '?')}")
        print(f"   RPD limit     : {h.get('x-ratelimit-limit-requests', '?')}")
        print(f"   TPM remaining : {h.get('x-ratelimit-remaining-tokens', '?')}")
        print(f"   RPD remaining : {h.get('x-ratelimit-remaining-requests', '?')}")

    print()
    print("=" * 78)
    print("WELL-KNOWN MODELS NOT LISTED ON THIS KEY (probed anyway)")
    print("=" * 78)
    for m in UNLISTED_PROBE:
        h, err = await get_limits(m)
        print(f"\n{m}")
        if err:
            print(f"   UNAVAILABLE -> {err}")
            continue
        print(f"   TPM limit     : {h.get('x-ratelimit-limit-tokens', '?')}")
        print(f"   RPD limit     : {h.get('x-ratelimit-limit-requests', '?')}")
        print(f"   *** WORKS ***")


asyncio.run(main())