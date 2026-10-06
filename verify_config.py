"""Static verification that backend/.env wiring reaches every consumer."""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))

# Mirror the PYTHONPATH the real launcher (run.bat / start_throttle_server.ps1)
# sets, so importing api.py resolves every nested module package.
for _sub in (
    BASE,
    os.path.join(BASE, "llm-20260922T070723Z-1-001"),
    os.path.join(BASE, "db_less_optimizer-20260922T070953Z-1-001"),
    os.path.join(BASE, "validator-20260922T070723Z-1-001"),
    os.path.join(BASE, "utils-20260922T070723Z-1-001"),
):
    if _sub not in sys.path:
        sys.path.insert(0, _sub)

print("=" * 70)
print("STEP 1 - raw .env values (Gemini provider)")
print("=" * 70)
from dotenv import load_dotenv
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(env_path, override=True)
for k in ("GEMINI_SA_FILE", "GEMINI_PROJECT", "GEMINI_LOCATION", "GEMINI_MODEL",
          "GEMINI_TIMEOUT", "GEMINI_MAX_429_RETRIES", "GEMINI_INTERCALL_GAP",
          "GEMINI_STAGE_PAUSE_SECONDS", "LLM_TIMEOUT_SECONDS"):
    print(f"  {k:26s} = {os.getenv(k)}")
import os as _os
_sa = os.getenv("GEMINI_SA_FILE", "")
print(f"  {'SA_FILE_EXISTS':26s} = {_os.path.isfile(_os.path.join(BASE, _sa))} ({_sa})")

print()
print("=" * 70)
print("STEP 2 - llm.py module constants (Gemini client)")
print("=" * 70)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "llm-20260922T070723Z-1-001"))
import llm.llm as L
print(f"  _GEMINI_MODEL       = {L._GEMINI_MODEL}")
print(f"  _GEMINI_LOCATION    = {L._GEMINI_LOCATION}")
print(f"  _GEMINI_PROJECT     = {L._GEMINI_PROJECT}")
print(f"  _GEMINI_SA_FILE     = {L._GEMINI_SA_FILE}")
print(f"  _MAX_429_RETRIES    = {L._MAX_429_RETRIES}")
print(f"  _GEMINI_TIMEOUT     = {L._GEMINI_TIMEOUT}")

print()
print("=" * 70)
print("STEP 3 — api.py module constants (timeout + stage pacing)")
print("=" * 70)
import api
print(f"  LLM_TIMEOUT_SECONDS   = {api.LLM_TIMEOUT_SECONDS}")
print(f"  STAGE_PAUSE_SECONDS   = {api.STAGE_PAUSE_SECONDS}")
print(f"  MAX_CONCURRENT        = {api.MAX_CONCURRENT_PIPELINES}")
print(f"  QUEUE_TIMEOUT_SECONDS = {api.QUEUE_TIMEOUT_SECONDS}")

print()
print("=" * 70)
print("STEP 4 — safe_print behaviour on non-cp1252 chars")
print("=" * 70)
from utils.utils import safe_print, parse_llm_json
# U+2011 non-breaking hyphen and U+2014 em dash are the documented crash chars
safe_print("  non-breaking hyphen: 'high\u2011traffic'")
safe_print("  em dash:             'stage 2 \u2014 planner'")
safe_print("  smart quotes:        \u201cindex scan\u201d")
print("  -> safe_print survived all non-cp1252 characters")

print()
print("=" * 70)
print("STEP 5 - parse_llm_json on a realistic gemini response")
print("=" * 70)
sample = ('{"issues":["SELECT * retrieves unnecessary columns"],'
          '"summary":"Adding a covering index on country reduces IO."}')
out = parse_llm_json(sample)
print(f"  keys={list(out.keys())}")
print(f"  parsed_ok={bool(out)}")

print()
print("ALL CHECKS PASSED")
