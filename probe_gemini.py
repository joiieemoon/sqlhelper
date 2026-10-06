"""Probe the original Gemini (Vertex AI) auth path used by this backend.

Confirms which SDK entry point + location + model id actually answers, so
llm.py can be restored to the original Gemini provider without guessing.
"""
import json
import os
import sys
import traceback

BASE = os.path.dirname(os.path.abspath(__file__))
SA_FILE = os.path.join(BASE, "fish-store-488312-4164e0ad13ad.json")

with open(SA_FILE, "r", encoding="utf-8") as fh:
    sa = json.load(fh)

PROJECT = sa["project_id"]
print(f"service account : {sa['client_email']}")
print(f"project_id      : {PROJECT}")
print()

from google.oauth2 import service_account

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
creds = service_account.Credentials.from_service_account_file(SA_FILE, scopes=SCOPES)

MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash-001",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-2.5-pro",
]
LOCATIONS = ["us-central1", "global"]

from google import genai

for location in LOCATIONS:
    print("=" * 70)
    print(f"LOCATION = {location}  (vertexai=True, service account)")
    print("=" * 70)
    try:
        client = genai.Client(
            vertexai=True,
            project=PROJECT,
            location=location,
            credentials=creds,
        )
    except Exception as exc:
        print(f"  client init FAILED: {type(exc).__name__}: {exc}")
        continue

    for model in MODELS:
        try:
            resp = client.models.generate_content(
                model=model,
                contents="Reply with exactly this JSON and nothing else: {\"ok\": true}",
                config={
                    "temperature": 0.1,
                    "response_mime_type": "application/json",
                },
            )
            text = (resp.text or "").strip()
            print(f"  {model:22s} OK   -> {text[:80]!r}")
        except Exception as exc:
            msg = str(exc).replace("\n", " ")[:160]
            print(f"  {model:22s} FAIL -> {type(exc).__name__}: {msg}")
    print()

print("=" * 70)
print("PROBE COMPLETE")
print("=" * 70)
