import re
import json
import sys


def _configure_utf8_stdio() -> None:
    """
    Force UTF-8 on stdout/stderr so printing raw LLM text never raises.

    On Windows the console/redirected-file encoding defaults to cp1252. Models
    routinely emit characters outside that codepage (U+2011 non-breaking hyphen,
    U+201C/U+201D smart quotes, U+00A0 nbsp). A bare print() of such a response
    raises UnicodeEncodeError, which the caller catches as a generic LLM failure
    and retries — burning quota and a full pipeline stage.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # Non-reconfigurable stream (e.g. an already-wrapped capture).
            pass


_configure_utf8_stdio()


def safe_print(*args, **kwargs) -> None:
    """
    print() that degrades to a lossy but non-raising write.

    Logging must never be able to abort an LLM call: an exception raised while
    printing a response is caught by the caller as a generic LLM failure, which
    triggers a retry and burns tokens against the per-minute budget.
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


# Retained for callers that still reference the private name.
_safe_print = safe_print


def clean_sql(query: str) -> str:
    if not query:
        return query
    query = query.replace("\\n", " ").replace("\n", " ")
    query = query.replace("\t", " ").replace("\r", " ")
    query = re.sub(r"\s+", " ", query)
    query = re.sub(r"\s+,", ",", query)
    query = query.encode().decode("unicode_escape")
    query = re.sub(r"\s+", " ", query).strip()
    return query

def parse_llm_json(response) -> dict:
    if isinstance(response, dict):
        return response
    if not response:
        return {}
    if isinstance(response, str):
        try:
            cleaned = re.sub(r"```json|```", "", response).strip()
            cleaned = re.sub(r'//[^\n]*', '', cleaned)
            cleaned = re.sub(r',\s*}', '}', cleaned)
            cleaned = re.sub(r',\s*]', ']', cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned.startswith("{{"):
                cleaned = cleaned[1:-1]
            return json.loads(cleaned)
        except Exception as e:
            safe_print("JSON PARSE ERROR:", e)
            return {}
    return {}

def _is_rate_limit_error(err) -> bool:
    """True when the error is a provider 429 / token-per-minute rejection."""
    text = str(err).lower()
    return (
        "429" in text
        or "rate limit" in text
        or "rate_limit_exceeded" in text
        or "too many requests" in text
        or "tokens per minute" in text
    )

async def safe_json_call(call_llm, prompt: str, system_prompt: str) -> dict:
    retries   = 2
    last_error = None

    for attempt in range(retries + 1):
        try:
            response = await call_llm(prompt, system_prompt)

            safe_print("\n--- LLM RAW RESPONSE ---")
            safe_print(response)
            safe_print("------------------------\n")

            parsed = parse_llm_json(response)
            if parsed:
                return parsed

            last_error = "Empty or invalid JSON"

        except Exception as e:
            safe_print(f"LLM CALL ERROR (attempt {attempt + 1}): {e}")
            last_error = str(e)

            # call_llm already exhausted its own TPM back-off budget for rate
            # limits. Retrying here would multiply those long waits by 3, blow
            # past the request timeout, and burn more of the per-minute quota.
            if _is_rate_limit_error(e):
                safe_print(
                    "[utils.py] rate-limit error — call_llm already exhausted its "
                    "back-off budget; not retrying to avoid multiplying the wait."
                )
                break

    return {"error": f"LLM parsing failed after {retries + 1} attempts: {last_error}"}