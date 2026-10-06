"""Measure real input/output tokens per Gemini 2.5 Flash call.

This is the atomic data the throttled llm.py must have: without actual usage
numbers, any TPM ceiling is a guess. We send the EXACT prompt/formatter shape
the pipeline will use (system + user), then read usage metadata.

NOTE: google-genai's generate_content returns usage in some response shapes.
We try the documented accessors first, then the raw response object.
"""
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
SA_FILE = os.path.join(BASE, "fish-store-488312-4164e0ad13ad.json")

with open(SA_FILE, "r", encoding="utf-8") as fh:
    sa = json.load(fh)

PROJECT = sa["project_id"]
from google.oauth2 import service_account
creds = service_account.Credentials.from_service_account_file(
    SA_FILE, scopes=["https://www.googleapis.com/auth/cloud-platform"]
)

from google import genai

# Use a realistic system prompt + user prompt (small, but same shape).
SYSTEM  = "You are a SQL analyst. Reply only with JSON."
USER    = "Reply exactly with this JSON and nothing else: {\"ok\": true}"

print(f"system_chars = {len(SYSTEM)}")
print(f"user_chars   = {len(USER)}")
print("-" * 70)

client = genai.Client(vertexai=True, project=PROJECT, location="us-central1", credentials=creds)

total_in = 0
total_out = 0
n = 0
for i in range(5):
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=USER,
        config={
            "temperature": 0.0,
            "response_mime_type": "application/json",
            "system_instruction": SYSTEM,
        },
    )
    t0 = time.monotonic()
    # Try to read usage off the response.
    usage_str = "n/a"
    inp = None
    out = None
    try:
        # google-genai usage lives on the response's usage_metadata attribute
        # on recent SDK versions.
        um = getattr(resp, "usage_metadata", None)
        if um is not None:
            inp = getattr(um, "input_token_count", None)
            out = getattr(um, "output_token_count", None)
            total_in  += (inp or 0)
            total_out += (out or 0)
            usage_str = f"in={inp} out={out}"
    except Exception as exc:
        usage_str = f"(usage read failed: {type(exc).__name__})"
    n += 1
    print(f"  [{i+1}] OK  {usage_str}")

print("-" * 70)
print(f"prompts  = {n}")
print(f"total in  = {total_in}  (avg {total_in/n:.1f}/call)")
print(f"total out = {total_out} (avg {total_out/n:.1f}/call)")

print()
print("If usage metadata was 'n/a' above, the SDK version omits it — then we")
print("fall back to character-based estimation (1 token ≈ 4 chars) in llm.py.")
print("That fallback is intentionally conservative (overestimates), which is")
print("safe for a ceiling: it only makes pacing slower, never burns real quota.")
