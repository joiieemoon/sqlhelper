import asyncio
import os
import re
import sys
import time


# ══════════════════════════════════════════════════════════════════════════
# SAFE STDIO
# ══════════════════════════════════════════════════════════════════════════
# This module prints at import time (key fingerprint, warnings) and again on
# every throttle/back-off event. On Windows the console and any redirected
# stdout default to cp1252, but gpt-oss routinely emits characters outside
# that codepage (U+2011 non-breaking hyphen, U+201C/U+201D smart quotes,
# U+2014 em dash). A bare print() of such text raises UnicodeEncodeError,
# which the pipeline catches as a generic LLM failure and retries — burning
# quota, a whole pipeline stage, and pushing the account toward the very 429
# this module exists to avoid.
#
# This runs before every print in the file, including the module-level ones
# below, so it is deliberately self-contained rather than relying on
# utils.py having been imported first (import order varies by caller).


def _configure_utf8_stdio() -> None:
    """Force UTF-8 on stdout/stderr so printing raw LLM text never raises."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # Non-reconfigurable stream (e.g. an already-wrapped capture).
            pass


_configure_utf8_stdio()


def _safe_print(*args, **kwargs) -> None:
    """
    print() that degrades to a lossy but non-raising write.

    Logging must never be able to abort an LLM call: a crash here costs a
    retry, and a retry costs tokens against the per-minute budget.
    """
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        try:
            text = " ".join(str(a) for a in args)
            print(text.encode(enc, "replace").decode(enc, "replace"), **kwargs)
        except Exception:
            pass
    except Exception:
        pass


# ── Load .env for provider credentials ────────────────────────────────────
# backend/.env is resolved relative to THIS file rather than the working
# directory. cwd-based discovery silently fails when launched from the repo
# root, an IDE debugger, or a scheduled task.
from dotenv import load_dotenv
import json as _json

_BACKEND_DIR = os.path.dirname(  # backend/
    os.path.dirname(  # llm-... (module package root)
        os.path.dirname(os.path.abspath(__file__))  # llm-.../llm
    )
)
_ENV_FILE = os.path.join(_BACKEND_DIR, ".env",)

if os.path.isfile(_ENV_FILE):
    # override=True so backend/.env is authoritative. Without it, a stale
    # GROQ_API_KEY left in the shell/OS environment (e.g. from an earlier test
    # session) silently wins and every call fails with 401 Invalid API Key.
    load_dotenv(_ENV_FILE, override=True)
else:
    # Fall back to the default search (cwd upwards) if the file is missing.
    load_dotenv()

# ── Gemini (Vertex AI) LLM bootstrap ──────────────────────────────────────
# google-genai + service-account JSON (no API key). Tuned via backend/.env:
#   GEMINI_SA_FILE / GEMINI_PROJECT / GEMINI_LOCATION / GEMINI_MODEL

_GEMINI_SA_FILE  = os.getenv("GEMINI_SA_FILE", "fish-store-488312-4164e0ad13ad.json")
_GEMINI_LOCATION = os.getenv("GEMINI_LOCATION", "us-central1")
_GEMINI_MODEL    = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
_GEMINI_TIMEOUT  = float(os.getenv("GEMINI_TIMEOUT", "120"))
_MAX_429_RETRIES = int(os.getenv("GEMINI_MAX_429_RETRIES",
                                 os.getenv("GROQ_MAX_429_RETRIES", "5")))
_WINDOW_SECONDS  = 60.0

if _GEMINI_SA_FILE and not os.path.isabs(_GEMINI_SA_FILE):
    _GEMINI_SA_FILE = os.path.join(_BACKEND_DIR, _GEMINI_SA_FILE)

_GEMINI_PROJECT = os.getenv("GEMINI_PROJECT", "")
if not _GEMINI_PROJECT and os.path.isfile(_GEMINI_SA_FILE or ""):
    try:
        with open(_GEMINI_SA_FILE, "r", encoding="utf-8") as _fh:
            _GEMINI_PROJECT = _json.load(_fh).get("project_id", "")
    except Exception:
        _GEMINI_PROJECT = ""

if os.path.isfile(_GEMINI_SA_FILE or ""):
    _safe_print(
        f"[llm.py] Gemini provider — model={_GEMINI_MODEL}, "
        f"location={_GEMINI_LOCATION}, project={_GEMINI_PROJECT}",
        flush=True,
    )
else:
    _safe_print(
        f"[llm.py] WARNING: Gemini SA file not found: {_GEMINI_SA_FILE}.",
        flush=True,
    )

# Lazy client: importing google suites at module scope would crash api.py
# at startup on machines without the packages.
_GEMINI_CLIENT = None
_GEMINI_AVAILABLE = False
_GEMINI_INIT_ERROR = ""


def _get_client():
    global _GEMINI_CLIENT, _GEMINI_AVAILABLE, _GEMINI_INIT_ERROR
    if _GEMINI_CLIENT is not None:
        return _GEMINI_CLIENT
    try:
        from google import genai
        from google.oauth2 import service_account
        if not os.path.isfile(_GEMINI_SA_FILE or ""):
            _GEMINI_INIT_ERROR = f"SA file not found: {_GEMINI_SA_FILE}"
            return None
        creds = service_account.Credentials.from_service_account_file(
            _GEMINI_SA_FILE,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        _GEMINI_CLIENT = genai.Client(
            vertexai=True, project=_GEMINI_PROJECT,
            location=_GEMINI_LOCATION, credentials=creds,
        )
        _GEMINI_AVAILABLE = True
        return _GEMINI_CLIENT
    except ImportError as exc:
        _GEMINI_INIT_ERROR = f"missing package: {exc}"
    except Exception as exc:
        _GEMINI_INIT_ERROR = f"{type(exc).__name__}: {exc}"
    _safe_print(f"[llm.py] WARNING: Gemini init failed: {_GEMINI_INIT_ERROR}")
    return None


# Backwards-compat names read by verify_config.py / old scripts.
_TPM_LIMIT = int(os.getenv("GROQ_TPM_LIMIT", "8000"))
_TPM_MARGIN = int(os.getenv("GROQ_TPM_MARGIN", "600"))
_EFFECTIVE_TPM = max(1000, _TPM_LIMIT - _TPM_MARGIN)
_GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
_GROQ_AVAILABLE = False
_GROQ_CLIENT = None

# LIGHT PACING (Vertex quota is generous; no TPM ledger needed).
# Probe: 15 rapid calls succeed, so only a short inter-call gap is kept.

_INTERCALL_GAP = float(os.getenv("GEMINI_INTERCALL_GAP", "1.0"))
_last_call_ts = 0.0
_pace_lock = asyncio.Lock()


def estimate_tokens(text):
    """Conservative token estimate."""
    return max(1, len(text or "") // 4)


async def _pace():
    global _last_call_ts
    async with _pace_lock:
        now = time.monotonic()
        wait = _INTERCALL_GAP - (now - _last_call_ts)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_ts = time.monotonic()


def _generate_text(client, prompt, system_prompt):
    """Blocking Vertex call - always runs inside asyncio.to_thread."""
    resp = client.models.generate_content(
        model=_GEMINI_MODEL,
        contents=prompt,
        config={
            "temperature": 0.1,
            "response_mime_type": "application/json",
            "system_instruction": system_prompt,
        },
    )
    return (getattr(resp, "text", None) or "").strip()


def _is_rate_limit(err: Exception) -> bool:
    """True if the exception is a 429 / quota-exhausted rejection."""
    text = str(err).lower()
    if getattr(err, "status_code", None) == 429:
        return True
    if getattr(err, "code", None) == 429:
        return True
    return (
        "rate limit" in text
        or "rate_limit_exceeded" in text
        or "resource_exhausted" in text
        or "429" in text
        or "too many requests" in text
        or "quota" in text
    )


def _retry_after_seconds(err: Exception, fallback: float) -> float:
    """
    Read the server-supplied `retry-after` header,
    falling back to the "... try again in 39.2s" hint in the message body.
    """
    response = getattr(err, "response", None)
    headers  = getattr(response, "headers", None) if response is not None else None
    if headers:
        try:
            raw = headers.get("retry-after")
        except Exception:
            raw = None
        if raw:
            try:
                return float(raw) + 0.5
            except (TypeError, ValueError):
                pass

    text = str(err)

    # "Please try again in 2m59.5s." — requests-per-minute style hint.
    match = re.search(r"try again in (\d+)m([0-9.]+)s", text)
    if match:
        try:
            return int(match.group(1)) * 60 + float(match.group(2)) + 0.5
        except ValueError:
            pass

    # "Please try again in 39.2s." — tokens-per-minute style hint.
    match = re.search(r"try again in ([0-9.]+)s", text)
    if match:
        try:
            return float(match.group(1)) + 0.5
        except ValueError:
            pass

    return fallback


async def call_llm(prompt: str, system_prompt: str):
    """Send a prompt to Gemini (Vertex AI) and return the JSON text.

    Same (prompt, system_prompt) -> text contract the pipeline expects,
    so issue_finder / planner / rewriter need no changes.
    """
    client = _get_client()
    if client is None:
        reason = _GEMINI_INIT_ERROR or "SA file missing"
        return '{"error": "Gemini client not initialized - %s"}' % reason
    last_error = None
    for attempt in range(1, _MAX_429_RETRIES + 1):
        await _pace()
        try:
            text = await asyncio.wait_for(
                asyncio.to_thread(_generate_text, client, prompt, system_prompt),
                timeout=_GEMINI_TIMEOUT,
            )
            if not text:
                raise RuntimeError("Gemini returned an empty response")
            return text
        except Exception as err:
            if isinstance(err, asyncio.TimeoutError):
                raise TimeoutError(
                    "Gemini call timed out after %.0fs" % _GEMINI_TIMEOUT
                ) from err
            if not _is_rate_limit(err):
                raise
            last_error = err
            wait = _retry_after_seconds(err, fallback=min(_WINDOW_SECONDS, 5.0 * attempt))
            _safe_print(
                "[llm.py] Gemini 429/quota (attempt %d/%d) - backing off %.1fs"
                % (attempt, _MAX_429_RETRIES, wait),
                flush=True,
            )
            await asyncio.sleep(wait)
            continue
    raise last_error if last_error else RuntimeError("Gemini call failed")
