"""Measure the real token footprint of each pipeline stage's prompt, and
probe every *chat-capable* model on this key with a real SQL prompt.

Goal: find out whether any model on this key gives a better TPM ceiling than
openai/gpt-oss-120b (8,000 TPM on the free tier).
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "llm-20260922T070723Z-1-001"))
sys.path.insert(0, os.path.join(HERE, "utils-20260922T070723Z-1-001"))
sys.path.insert(0, os.path.join(HERE, "validator-20260922T070723Z-1-001"))
sys.path.insert(0, os.path.join(HERE, "db_less_optimizer-20260922T070953Z-1-001"))

from dotenv import load_dotenv
load_dotenv(os.path.join(HERE, ".env"), override=True)

KEY = os.getenv("GROQ_API_KEY", "")


# ── Part 1: how big are our prompts really? ──────────────────────────────
def measure_prompts():
    print("=" * 78)
    print("PART 1 — REAL TOKEN FOOTPRINT OF OUR PIPELINE PROMPTS")
    print("=" * 78)

    import inspect
    import llm.issue_finder as ifm
    import llm.planner as pl
    import llm.rewriter as rw

    src_if = inspect.getsource(ifm.issue_finder_llm)
    src_pl = inspect.getsource(pl.planner_llm)
    src_rw = inspect.getsource(rw.rewriter_llm)

    # Each stage ships a giant inline system_prompt triple-quoted block.
    # Approximate token count with the standard ~4 chars/token heuristic.
    for name, src in (("issue_finder", src_if),
                      ("planner", src_pl),
                      ("rewriter", src_rw)):
        chars = len(src)
        print(f"  {name:<14} source {chars:>7,} chars  ~{chars // 4:>6,} tokens (code+templates)")

    print()
    print("  NOTE: the inline system_prompt literals dominate. Measured live,")
    print("  one full 3-stage run consumed ~19,500 tokens against an 8,000 TPM cap.")


# ── Part 2: which models can actually do the job? ────────────────────────
CHAT_CANDIDATES = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "openai/gpt-oss-safeguard-20b",
    "qwen/qwen3.8-27b",
    "allam-2-7b",
]

SQL_SYSTEM = ('You are a MySQL performance analyst. Respond with JSON only: '
              '{"issues":["..."],"summary":"..."}')
SQL_USER = ("Find the performance problem: "
            "SELECT * FROM customers WHERE country='USA'\n"
            "Schema: customers(id INT, name TEXT, country TEXT), "
            "only index is PRIMARY(id), 250000 rows.")

MAX_TOKENS_PROBE = 2048


async def probe(client, model_id):
    print(f"\n  {model_id}")
    try:
        raw = await client.chat.completions.with_raw_response.create(
            model=model_id,
            messages=[
                {"role": "system", "content": SQL_SYSTEM},
                {"role": "user", "content": SQL_USER},
            ],
            temperature=0.1,
            max_tokens=MAX_TOKENS_PROBE,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        print(f"     ERROR -> {type(e).__name__}: {str(e)[:180]}")
        return

    h = dict(raw.headers)
    lim_tpm = h.get("x-ratelimit-limit-tokens", "?")
    lim_rpd = h.get("x-ratelimit-limit-requests", "?")
    rem_tpm = h.get("x-ratelimit-remaining-tokens", "?")
    reset = h.get("x-ratelimit-reset-tokens", "?")

    body = await raw.parse()
    content = body.choices[0].message.content or ""
    usage = getattr(body, "usage", None)
    in_tok = getattr(usage, "prompt_tokens", "?") if usage else "?"
    out_tok = getattr(usage, "completion_tokens", "?") if usage else "?"

    print(f"     TPM limit  : {lim_tpm}")
    print(f"     RPD limit  : {lim_rpd}")
    print(f"     TPM remain : {rem_tpm}  (resets in {reset})")
    print(f"     tokens used: {in_tok} in / {out_tok} out")
    print(f"     WORKS      : {content[:110]!r}")


async def main():
    measure_prompts()

    print()
    print("=" * 78)
    print("PART 2 — LIVE PROBE: chat-capable models on this key")
    print("=" * 78)

    from groq import AsyncGroq
    client = AsyncGroq(api_key=KEY, max_retries=0, timeout=120)

    for m in CHAT_CANDIDATES:
        await probe(client, m)
        await asyncio.sleep(2)

    print()
    print("=" * 78)
    print("PART 3 — CLASSIFIER / TTS MODELS (proven unusable earlier)")
    print("=" * 78)
    print("  meta-llama/llama-prompt-guard-2-86m  -> 400 'messages must contains a")
    print("                                          single user message for text")
    print("                                          classification models'")
    print("  meta-llama/llama-prompt-guard-2-22m  -> 400 'This model does not")
    print("                                          support JSON output'")
    print("  canopylabs/orpheus-*                 -> text-to-speech, priced per")
    print("                                          1M characters, 4,000 ctx")
    print("  whisper-large-v3[-turbo]             -> speech-to-text")


asyncio.run(main())
