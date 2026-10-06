"""Groq model advisor — which model on this key can actually do the SQL job,
and what are its real rate limits?

Run:  python check_groq_key.py

Why this exists: on the Groq free tier EVERY chat model shares the same
8,000 TPM ceiling, while the models with a *higher* TPM (15K) are
prompt-injection classifiers that cannot generate SQL. Picking a model by
its advertised TPM alone is a trap — this tool shows the real picture.
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from dotenv import load_dotenv

load_dotenv(os.path.join(HERE, ".env"), override=True)

KEY = os.getenv("GROQ_API_KEY", "")

# ── Model taxonomy ───────────────────────────────────────────────────────
# Only CHAT models can serve the pipeline. The rest are listed so the tool
# explains *why* an attractive-looking TPM number is unusable.
NON_CHAT = {
    "meta-llama/llama-prompt-guard-2-86m": "prompt-injection CLASSIFIER (512-token ctx, no JSON mode)",
    "meta-llama/llama-prompt-guard-2-22m": "prompt-injection CLASSIFIER (512-token ctx, no JSON mode)",
    "canopylabs/orpheus-v1-english":       "TEXT-TO-SPEECH (audio output, not text)",
    "canopylabs/orpheus-arabic-saudi":     "TEXT-TO-SPEECH (audio output, not text)",
    "whisper-large-v3":                    "SPEECH-TO-TEXT (audio input, not text)",
    "whisper-large-v3-turbo":              "SPEECH-TO-TEXT (audio input, not text)",
}

# A minimal prompt that costs ~160 tokens, so probing every model stays well
# inside the 8,000 TPM window even on a cold account.
PROBE_SYSTEM = ('You are a MySQL performance analyst. Respond with JSON only: '
                '{"issues":["..."],"summary":"..."}')
PROBE_USER = ("Find the performance problem: "
              "SELECT * FROM customers WHERE country='USA'\n"
              "Schema: customers(id INT, name TEXT, country TEXT), "
              "only index is PRIMARY(id), 250000 rows.")


def list_models():
    from groq import Groq

    print("=" * 96)
    print("MODELS AVAILABLE ON THIS KEY")
    print("=" * 96)
    models = sorted(Groq(api_key=KEY).models.list().data, key=lambda m: m.id)

    for m in models:
        note = NON_CHAT.get(m.id)
        tag = "  [NOT TEXT GEN]" if note else "  [chat]"
        print(f"  {m.id:<40}{tag}")
        if note:
            print(f"      -> {note}")

    print(f"\n  Total: {len(models)} models")
    return {m.id for m in models}

async def probe(model_id):
    """Live-probe one model and return (limits, error)."""
    from groq import AsyncGroq

    client = AsyncGroq(api_key=KEY, max_retries=0, timeout=120)
    try:
        raw = await client.chat.completions.with_raw_response.create(
            model=model_id,
            messages=[
                {"role": "system", "content": PROBE_SYSTEM},
                {"role": "user", "content": PROBE_USER},
            ],
            temperature=0.1,
            max_tokens=2048,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:160]}"

    h = dict(raw.headers)
    body = await raw.parse()
    usage = getattr(body, "usage", None)
    content = (body.choices[0].message.content or "").strip()

    limits = {
        "tpm":        h.get("x-ratelimit-limit-tokens", "?"),
        "rpd":        h.get("x-ratelimit-limit-requests", "?"),
        "in":         getattr(usage, "prompt_tokens", "?") if usage else "?",
        "out":        getattr(usage, "completion_tokens", "?") if usage else "?",
        "valid_json": content.startswith("{") and content.endswith("}"),
    }
    return limits, None


async def probe_classifier(model_id):
    """Show what a 15K-TPM 'classifier' model actually returns.

    These models advertise 15,000 TPM (vs our 8,000) which makes them look
    like an upgrade. This probe proves they emit a bare probability float and
    zero completion tokens, so the token budget is unusable for SQL work.
    """
    from groq import AsyncGroq

    client = AsyncGroq(api_key=KEY, max_retries=0, timeout=120)
    results = []

    # Case 1: our real call shape (system + user). Expected to be REJECTED.
    try:
        await client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": PROBE_SYSTEM},
                {"role": "user", "content": PROBE_USER},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        results.append(("system+user + json mode (our call shape)", "ACCEPTED"))
    except Exception as e:
        results.append(("system+user + json mode (our call shape)",
                        f"REJECTED — {str(e)[:110]}"))

    # Case 2: single user message — its native contract. Returns a float.
    try:
        raw = await client.chat.completions.with_raw_response.create(
            model=model_id,
            messages=[{"role": "user", "content": PROBE_USER}],
            temperature=0.1,
        )
        h = dict(raw.headers)
        body = await raw.parse()
        usage = getattr(body, "usage", None)
        out = getattr(usage, "completion_tokens", "?") if usage else "?"
        content = (body.choices[0].message.content or "").strip()
        results.append(("single user message (its native contract)",
                        f"returns {content!r} — {out} output tokens"))
        results.append(("  its advertised TPM limit",
                        f"{h.get('x-ratelimit-limit-tokens', '?')} "
                        f"(RPD {h.get('x-ratelimit-limit-requests', '?')})"))
    except Exception as e:
        results.append(("single user message (its native contract)",
                        f"FAILED — {str(e)[:110]}"))

    return results


async def main():
    if not KEY:
        print("ERROR: GROQ_API_KEY is not set. Add it to backend/.env")
        return

    print(f"Key: {KEY[:10]}...{KEY[-4:]}  (len={len(KEY)})\n")
    list_models()
    print()

    targets = [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "openai/gpt-oss-safeguard-20b",
        "qwen/qwen3.8-27b",
        "allam-2-7b",
        # Historically free Groq chat models — probed to see whether this org
        # has access even when /models does not list them.
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
    ]

    print("=" * 96)
    print("LIVE PROBE — CHAT MODELS (real limits from response headers)")
    print("=" * 96)
    print(f"  {'MODEL':<38}{'TPM':>7}{'RPD':>8}{'tokens':>14}  USABLE")
    print("  " + "-" * 90)

    best = None
    for model_id in targets:
        limits, err = await probe(model_id)

        if err:
            short = err.replace("\n", " ")[:42]
            print(f"  {model_id:<38}{'-':>7}{'-':>8}{'-':>14}  NO  ({short})")
            await asyncio.sleep(1)
            continue

        used = f"{limits['in']}in/{limits['out']}out"
        usable = "YES" if limits["valid_json"] else "no (bad JSON)"
        print(f"  {model_id:<38}{limits['tpm']:>7}{limits['rpd']:>8}{used:>14}  {usable}")

        try:
            tpm = int(limits["tpm"])
        except (TypeError, ValueError):
            tpm = -1
        if limits["valid_json"] and (best is None or tpm > best[1]):
            best = (model_id, tpm)

        await asyncio.sleep(2)

    # ── The 15K TPM trap, demonstrated live ──────────────────────────────
    print()
    print("=" * 96)
    print("THE 15,000 TPM TRAP — why the higher-limit models cannot be swapped in")
    print("=" * 96)
    for model_id in ("meta-llama/llama-prompt-guard-2-86m",
                     "meta-llama/llama-prompt-guard-2-22m"):
        print(f"\n  {model_id}")
        for label, outcome in await probe_classifier(model_id):
            print(f"      {label:<45} {outcome}")
        await asyncio.sleep(2)
    print()
    print("  These are prompt-injection CLASSIFIERS. They emit one probability")
    print("  float with completion_tokens=0 — no JSON, no SQL, no text. Their")
    print("  bigger output budget is irrelevant because there is no output.")

    print()
    print("=" * 96)
    print("VERDICT")
    print("=" * 96)
    if best:
        print(f"  Highest-TPM working chat model : {best[0]}  ({best[1]:,} TPM)")
        print(f"  Currently configured (GROQ_MODEL): {os.getenv('GROQ_MODEL', 'unset')}")
        print()
        print("  The 3-stage pipeline needs ~19,500 tokens per request, so on the")
        print("  free tier every run must spread across ~3 one-minute windows.")
        print("  Pacing is handled by GROQ_STAGE_PAUSE_SECONDS plus the TPM")
        print("  throttle in llm.py. Swapping models cannot change this.")
    else:
        print("  No usable chat model found on this key.")

    print()
    print("  Models with a HIGHER advertised TPM on this key are classifiers,")
    print("  not chat models — see the [NOT TEXT GEN] entries above.")
    print("  To raise the ceiling itself you need the Groq Developer plan,")
    print("  a different provider, or fewer/shorter prompts.")


asyncio.run(main())


