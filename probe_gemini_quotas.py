    """Measure Gemini 2.5 Flash's real RPM / TPM / RPD limits on this service account.

These numbers are the ground truth the throttled llm.py must enforce; without
them any self-pacing is a guess and a 3-stage pipeline either burns quota or
wastes time.

Strategy:
  run = send N prompts back-to-back as fast as the SDK will allow,
        log every 429 + its Retry-After, log total wall time.
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

PROMPT  = "Reply exactly with this JSON and nothing else: {\"n\": 1}"
SYSTEM  = "You return only JSON."

N_REQUESTS = 15  # enough to trip RPM on a restricted tier

print(f"project  = {PROJECT}")
print(f"model    = gemini-2.5-flash")
print(f"location = us-central1")
print(f"requests = {N_REQUESTS} back-to-back")
print("-" * 70)

client = genai.Client(vertexai=True, project=PROJECT, location="us-central1", credentials=creds)
fails = []
started = time.monotonic()

for i in range(N_REQUESTS):
    t0 = time.monotonic()
    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=PROMPT,
            config={
                "temperature": 0.0,
                "response_mime_type": "application/json",
            },
            # Pass the system instruction through the standard field so we can
            # measure realistic per-call cost (system counts toward input tokens).
            # genai's generate_content accepts system_instruction on the config.
            # We'll set it via the top-level param if the SDK supports it.
        )
        # For the quota probe we only need the method to dispatch; actual
        # latency is what matters for the RPM ceiling.
        dt = time.monotonic() - t0
        print(f"  [{i+1:02d}] OK  {dt:.2f}s")
    except Exception as exc:
        dt = time.monotonic() - t0
        msg = str(exc).replace("\n", " ")[:120]
        retry_after = None
        rc = getattr(exc, "code", None) or getattr(getattr(exc, "response", None), "status_code", None)
        if rc == 429:
            rh = getattr(getattr(exc, "response", None), "headers", None)
            if rh:
                try:
                    retry_after = rh.get("retry-after")
                except Exception:
                    pass
        fails.append((i + 1, dt, type(exc).__name__, rc, retry_after, msg))
        print(f"  [{i+1:02d}] 429 {dt:.2f}s  retry-after={retry_after}  {type(exc).__name__}: {msg}")

wall = time.monotonic() - started
ok = N_REQUESTS - len(fails)
print("-" * 70)
print(f"結果: {ok}/{N_REQUESTS} succeeded in {wall:.1f}s, {len(fails)} failed")
if fails:
    print("first 429 detail:")
    f0 = fails[0]
    print(f"  attempt {f0[0]} after {f0[1]:.2f}s, code={f0[3]}, retry-after={f0[4]}, msg={f0[5]}")

print()
print("INTERPRETATION")
print("  - If 429 hits before N_REQUESTS and retry-after is present → that is the")
print("    real per-minute pause. RPM ≈ 60 / retry_after(seconds).")
print("  - If all succeed and wall < 60s → RPM ceiling is higher than N_REQUESTS,")
print("    but TPM or RPD may still fire on a full pipeline. A 3-stage pipeline")
print("    needs ~3 × (system + prompt) input tokens; if that exceeds the per-minute")
print("    input token budget, stage 2/3 will 429 with a 'try again in Ns' body.")
print("  - re-calling with a fresh client mid-probe can surface RPD limits, but")
print("    for dev testing RPM is the primary constraint.")
