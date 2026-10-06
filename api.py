from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware

import api_trace
from contextlib import asynccontextmanager
from typing import Any

from llm.issue_finder import issue_finder_llm
from llm.planner import planner_llm
from llm.rewriter import rewriter_llm
from llm.explain_summarizer import explain_summarizer_llm
from db_less_optimizer.planner import syntax_only_planner_llm
from db_less_optimizer.rewriter import syntax_only_rewriter_llm
from validator.query_validator import validate_query
from rate_limiter import check_and_increment, get_usage, consume
from utils.utils import safe_print

import asyncio
import sqlparse
import json
import os
import time
import uuid
import logging
import difflib
import re

from logging.handlers import TimedRotatingFileHandler
from dotenv import load_dotenv

# ── Load backend/.env before any constant is read from the environment ──
# Resolved relative to this file so the values hold no matter which working
# directory uvicorn, run.bat, or the VS Code debugger was started from.
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_ENV_FILE    = os.path.join(_BACKEND_DIR, ".env")
if os.path.isfile(_ENV_FILE):
    load_dotenv(_ENV_FILE)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE_DIR, "log")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "api.log")

log = logging.getLogger("query_optimizer")
log.setLevel(logging.INFO)

if not log.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(console_handler)

    file_handler = TimedRotatingFileHandler(
        LOG_FILE,
        when="D",
        interval=1,
        backupCount=7,
        encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(file_handler)

MAX_CONCURRENT_PIPELINES = int(os.getenv("MAX_CONCURRENT_PIPELINES", "50"))
LLM_TIMEOUT_SECONDS      = int(os.getenv("LLM_TIMEOUT_SECONDS", "300"))
QUEUE_TIMEOUT_SECONDS    = int(os.getenv("QUEUE_TIMEOUT_SECONDS", "30"))
MIN_AGENT_VERSION        = 1

# ── Testing / development pacing ───────────────────────────────────────
# Deliberate pause between pipeline stages (issue finder → planner →
# rewriter). With Gemini there is no TPM ceiling to dodge; this is only a
# dev knob. Reads GEMINI_STAGE_PAUSE_SECONDS first, then the legacy GROQ_
# name. Set to 0 to disable (production speed).
STAGE_PAUSE_SECONDS = float(os.getenv(
    "GEMINI_STAGE_PAUSE_SECONDS",
    os.getenv("GROQ_STAGE_PAUSE_SECONDS", "0"),
))


async def pace_between_stages(req_id: str, next_stage: str) -> None:
    """
    Sleep between pipeline stages when testing-phase pacing is enabled.

    This is intentionally a plain sleep rather than a token-budget check: it
    lets the rolling 60s window in llm.py drain naturally so the next stage
    starts from a near-empty window instead of queueing behind this one.
    """
    if STAGE_PAUSE_SECONDS <= 0:
        return
    log.info(
        f"[{req_id}] Testing-phase pacing — waiting {STAGE_PAUSE_SECONDS:.0f}s "
        f"before {next_stage}"
    )
    await asyncio.sleep(STAGE_PAUSE_SECONDS)

_semaphore: asyncio.Semaphore | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _semaphore
    _semaphore = asyncio.Semaphore(MAX_CONCURRENT_PIPELINES)
    log.info(f"Server started — max concurrent pipelines: {MAX_CONCURRENT_PIPELINES}")
    yield
    log.info("Server shutting down.")


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── Identifier extraction ─────────────────────────────────────────────

def get_rate_limit_identifier(request: Request) -> str:
    """
    Primary:  X-Device-ID header — a UUID generated once per Chrome
              installation and stored in chrome.storage.local.
              Unique per person even on a shared office network.

    Fallback: Client IP — used if the header is missing (e.g. direct
              API calls, testing). Reads X-Forwarded-For for proxy/ngrok.
    """
    device_id = request.headers.get("X-Device-ID", "").strip()
    if device_id:
        return device_id

    # IP fallback
    forwarded_for = request.headers.get("X-Forwarded-For", "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    return request.client.host


# ── Middleware ────────────────────────────────────────────────────────

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """
    Human log line per request, plus a structured record in
    trace/api_trace.jsonl for developer review and accuracy checking.

    The response body is read here so it can be traced; it is re-attached to a
    new Response so downstream code (and the client) is unaffected.
    """
    req_id = str(uuid.uuid4())[:8]
    start  = time.time()
    log.info(f"[{req_id}] → {request.method} {request.url.path}")

    # Capture the request body without consuming it for the route handler.
    req_body = None
    if request.method in ("POST", "PUT", "PATCH"):
        try:
            raw = await request.body()
            if raw:
                req_body = json.loads(raw)
        except Exception:  # noqa: BLE001 - malformed JSON is handled by the route
            req_body = None

    client = request.client.host if request.client else ""
    device = request.headers.get("X-Device-ID", "")

    try:
        response = await call_next(request)

        body_bytes = b""
        try:
            async for chunk in response.body_iterator:
                body_bytes += chunk if isinstance(chunk, bytes) else str(chunk).encode()
        except Exception:  # noqa: BLE001
            pass

        resp_body = None
        if body_bytes:
            try:
                resp_body = json.loads(body_bytes)
            except Exception:  # noqa: BLE001 - non-JSON body (files, html)
                resp_body = None

        elapsed = round((time.time() - start) * 1000)
        log.info(f"[{req_id}] ← {response.status_code} ({elapsed}ms)")

        api_trace.record(
            req_id=req_id,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            elapsed_ms=elapsed,
            request_body=req_body,
            response_body=resp_body,
            device_id=device,
            client=client,
        )

        return Response(
            content=body_bytes,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )
    except Exception as e:
        elapsed = round((time.time() - start) * 1000)
        log.error(f"[{req_id}] ✕ Unhandled error ({elapsed}ms): {e}")
        api_trace.record(
            req_id=req_id,
            method=request.method,
            path=request.url.path,
            status=500,
            elapsed_ms=elapsed,
            request_body=req_body,
            response_body={"error": "Internal server error"},
            device_id=device,
            client=client,
            error=f"{type(e).__name__}: {e}",
        )
        return JSONResponse(status_code=500, content={"error": "Internal server error"})


# ── Helpers ───────────────────────────────────────────────────────────

def format_sql_for_display(query: str) -> str:
    try:
        return sqlparse.format(query, reindent=True, keyword_case="upper")
    except Exception:
        return query


# ── SQL change detection ──────────────────────────────────────────────

# Action types the rewriter acknowledges without ever touching the SQL text
# (CHECK C / CHECK D of the rewriter protocol). They land in applied_actions
# too, so counting applied_actions as "edits" overstates what happened.
NON_SQL_CHANGING_ACTIONS = {
    "NO_OP",
    "ANALYZE_TABLE",
    "UPDATE_HISTOGRAM",
    "CREATE_INDEX",
    "SUGGEST_COVERING_INDEX",
    "DROP_REDUNDANT_INDEX",
    "USE_COMPOSITE_KEY",
}


def sql_tokens(query: str):
    """
    Tokenize SQL for comparison.

    Returns (keys, display):
      * keys    — lowercased, whitespace-collapsed, semicolon-stripped tokens,
                  used for equality so layout/case/semicolon never count as a
                  change (both queries are reindented by the caller).
      * display — the same tokens in their original casing, so reported
                  fragments read like the SQL the caller actually sent.
    Both lists are index-aligned, so an offset found in `keys` also indexes
    `display`.
    """
    if not query:
        return [], []
    normalized = re.sub(r"\s+", " ", query.strip().rstrip(";")).strip()
    display = normalized.split()
    return [token.lower() for token in display], display


def diff_sql(original: str, optimized: str):
    """
    Compare an optimized query against the original.

    Returns (changed, changes). `changes` is a short human-readable list of
    the actual edits: `optimized_query` is a long reindented blob, so a single
    deleted predicate inside a WHERE clause is nearly invisible next to the
    original — which made a successful optimization look like a no-op.
    """
    before, before_display = sql_tokens(original)
    after, after_display   = sql_tokens(optimized)
    if before == after:
        return False, []

    changes = []
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        removed = " ".join(before_display[i1:i2])
        added   = " ".join(after_display[j1:j2])
        if tag == "delete":
            changes.append(f"removed: {removed}")
        elif tag == "insert":
            changes.append(f"added: {added}")
        else:
            changes.append(f"replaced: {removed} -> {added}")
    return True, changes


def ensure_dict(obj: Any) -> dict:
    if isinstance(obj, str):
        try:
            return json.loads(obj)
        except Exception:
            return {}
    return obj if isinstance(obj, dict) else {}


async def acquire_with_timeout(req_id: str) -> bool:
    try:
        await asyncio.wait_for(
            _semaphore.acquire(),
            timeout=QUEUE_TIMEOUT_SECONDS
        )
        return True
    except asyncio.TimeoutError:
        log.warning(
            f"[{req_id}] Timed out waiting for pipeline slot after {QUEUE_TIMEOUT_SECONDS}s"
        )
        return False


class LLMStageError(Exception):
    """
    A pipeline stage failed to produce a usable result.

    Raised when a stage hands back an error payload — in practice a provider
    rejection such as a Gemini 429/quota error, or a response that could not
    be parsed as JSON.

    Why this exists: without it each stage falls back to an empty list, so the
    endpoint cheerfully answers 200 with "No optimizations were applied to the
    query." That is indistinguishable from a genuine clean analysis, so a
    throttled run looks like a successful one. Surfacing the failure lets the
    client retry instead of trusting an empty result.
    """


def raise_on_stage_error(result: Any, stage: str) -> None:
    """Turn a stage's {'error': ...} payload into a hard LLMStageError."""
    if isinstance(result, dict) and result.get("error"):
        raise LLMStageError(f"{stage} failed — {result['error']}")


# ── Routes ────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    # Lazy import avoids hard google deps at module import time.
    try:
        from llm.llm import (
            _GEMINI_MODEL as _m, _GEMINI_LOCATION as _loc,
            _GEMINI_PROJECT as _proj,
        )
        llm_info = {"provider": "gemini-vertex", "model": _m,
                    "location": _loc, "project": _proj}
    except Exception as exc:
        llm_info = {"provider": "gemini-vertex", "error": str(exc)[:120]}
    return {
        "status":                "ok",
        "max_concurrent":        MAX_CONCURRENT_PIPELINES,
        "llm_timeout_seconds":   LLM_TIMEOUT_SECONDS,
        "queue_timeout_seconds": QUEUE_TIMEOUT_SECONDS,
        "llm":                   llm_info,
    }


@app.get("/usage")
def usage(request: Request):
    """
    Report the caller's daily optimization counter WITHOUT consuming a query.

    The UI calls this on load and after "Test Connection" so the counter the
    user sees always matches the server's authoritative state, and a
    credential check never burns one of the 5 daily optimizations.
    """
    identifier = get_rate_limit_identifier(request)
    return {"identifier": identifier, **get_usage(identifier)}


@app.post("/agent_optimize")
async def agent_optimize(data: dict, request: Request):
    req_id     = str(uuid.uuid4())[:8]
    identifier = get_rate_limit_identifier(request)

    log.info(f"[{req_id}] /agent_optimize Input: {json.dumps(data, default=str)}")

    agent_version = data.get("agent_version")
    if not isinstance(agent_version, int) or agent_version < MIN_AGENT_VERSION:
        log.warning(f"[{req_id}] Rejected outdated/missing agent_version: {agent_version!r}")
        err_res = {"error": "OUTDATED_AGENT", "message": "Your local agent is outdated. Please update to continue."}
        log.info(f"[{req_id}] /agent_optimize Output: {json.dumps(err_res, default=str)}")
        return JSONResponse(status_code=426, content=err_res)
    
    # ── Rate limit gate — CHECK ONLY, nothing is consumed yet ─────────
    # The single credit is spent in consume() after the LLM actually runs,
    # so a request rejected below (bad SQL, missing query, server busy)
    # never costs the user one of their 5 daily optimizations.
    rate = get_usage(identifier)
    if not rate.get("allowed", True):
        log.warning(f"[{req_id}] Rate limit hit — identifier: {identifier}")
        err_res = {
            "error":     rate["error"],
            "resets_in": rate["resets_in"],
            "limit":             rate["limit"],
            "queries_used":      rate["queries_used"],
            "queries_remaining": rate["queries_remaining"],
        }
        log.info(f"[{req_id}] /agent_optimize Output: {json.dumps(err_res, default=str)}")
        return JSONResponse(
            status_code=429,
            content=err_res,
        )

    log.info(
        f"[{req_id}] identifier={identifier} — "
        f"{rate['queries_used']}/{rate['limit']} queries used"
    )

    query = data.get("query", "").strip()
    if not query:
        err_res = {"error": "Query is required."}
        log.info(f"[{req_id}] /agent_optimize Output: {json.dumps(err_res, default=str)}")
        return err_res

    validation = validate_query(query=query)
    if validation != "FINE":
        err_res = {"error": validation}
        log.info(f"[{req_id}] /agent_optimize Output: {json.dumps(err_res, default=str)}")
        return err_res

    scope          = data.get("scope", [])
    custom_request = data.get("custom_request", "")
    schema         = ensure_dict(data.get("schema", {}))
    explain_raw    = data.get("explain_analyze", {})

    combined_analysis = {
        "innodb_stats": data.get("innodb_stats", {}),
    }

    optional_fields = [
        "covering_opportunities",
        "implicit_cast_issues",
        "function_wrapped_column_issues",
        "subquery_pattern_issues",
        "or_on_indexed_column_issues",
        "handler_stats",
        "column_statistics",
        "server_variables",
        "sys_index_stats",
        "optimizer_switch",
        "optimizer_trace",
        "optimizer_warnings",
        "optimizer_rewritten_query",
    ]

    for field in optional_fields:
        value = data.get(field)
        if value is not None:
            combined_analysis[field] = value

    acquired = await acquire_with_timeout(req_id)
    if not acquired:
        err_res = {"error": "Server is busy. Please try again in a moment."}
        log.info(f"[{req_id}] /agent_optimize Output: {json.dumps(err_res, default=str)}")
        return JSONResponse(
            status_code=503,
            content=err_res
        )

    start = time.time()
    log.info(f"[{req_id}] agent_optimize started")

    try:
        async def run_pipeline():
            issues_result = await issue_finder_llm(
                query, explain_raw, combined_analysis,
                scope, schema, custom_request,
            )
            raise_on_stage_error(issues_result, "issue analysis stage")

            issues = issues_result.get("issues", []) if isinstance(issues_result, dict) else []

            await pace_between_stages(req_id, "planning stage")

            action_result   = await planner_llm(
                query, issues, schema, scope, custom_request,
            )
            raise_on_stage_error(action_result, "planning stage")

            actions         = action_result.get("actions", [])         if isinstance(action_result, dict) else []
            # summary         = action_result.get("summary", "")         if isinstance(action_result, dict) else ""
            execution_order = action_result.get("execution_order", []) if isinstance(action_result, dict) else []

            await pace_between_stages(req_id, "rewrite stage")

            rewriter_result       = await rewriter_llm(
                query, schema, actions,
                custom_request, scope, execution_order,
            )
            raise_on_stage_error(rewriter_result, "rewrite stage")

            optimized_query       = rewriter_result.get("optimized_query", query) if isinstance(rewriter_result, dict) else query
            index_recommendations = rewriter_result.get("ddl_statements", [])     if isinstance(rewriter_result, dict) else []
            summary = rewriter_result.get("summary", [])
            
            safe_print("**" * 50)
            safe_print("The Summary of what changes has been done actually")
            safe_print(summary)
            safe_print("**" * 50)
            
            return {
                "optimized_query":       format_sql_for_display(optimized_query),
                "actions":               actions,
                "index_recommendations": index_recommendations,
                "summary":               summary,
                "error":                 None,
            }

        result  = await asyncio.wait_for(run_pipeline(), timeout=LLM_TIMEOUT_SECONDS)
        elapsed = round((time.time() - start) * 1000)
        log.info(f"[{req_id}] agent_optimize done ({elapsed}ms)")

        # ── LLM actually ran — NOW spend one credit ───────────────────
        rate = consume(identifier)

        # ── Attach usage counters to every successful response ─────────
        result["limit"]             = rate["limit"]
        result["queries_used"]      = rate["queries_used"]
        result["queries_remaining"] = rate["queries_remaining"]

        log.info(f"[{req_id}] /agent_optimize Output: {json.dumps(result, default=str)}")
        return result

    except asyncio.TimeoutError:
        elapsed = round((time.time() - start) * 1000)
        log.error(f"[{req_id}] agent_optimize timed out ({elapsed}ms)")
        err_res = {"error": "Request timed out. Your query may be too complex — try simplifying it."}
        log.info(f"[{req_id}] /agent_optimize Output: {json.dumps(err_res, default=str)}")
        return JSONResponse(
            status_code=504,
            content=err_res
        )

    except LLMStageError as e:
        elapsed = round((time.time() - start) * 1000)
        log.error(f"[{req_id}] agent_optimize LLM stage failed ({elapsed}ms): {e}")
        err_res = {
            "error":     str(e),
            "message":   "The analysis model is temporarily rate limited. Please retry in a few minutes.",
            "retryable": True,
        }
        log.info(f"[{req_id}] /agent_optimize Output: {json.dumps(err_res, default=str)}")
        return JSONResponse(status_code=503, content=err_res)

    except Exception as e:
        elapsed = round((time.time() - start) * 1000)
        log.error(f"[{req_id}] agent_optimize failed ({elapsed}ms): {e}")
        err_res = {"error": f"Optimization failed: {str(e)}"}
        log.info(f"[{req_id}] /agent_optimize Output: {json.dumps(err_res, default=str)}")
        return err_res

    finally:
        _semaphore.release()


@app.post("/optimize")
async def optimize(data: dict, request: Request):
    req_id     = str(uuid.uuid4())[:8]
    identifier = get_rate_limit_identifier(request)

    log.info(f"[{req_id}] /optimize Input: {json.dumps(data, default=str)}")

    # ── Rate limit gate — CHECK ONLY, nothing is consumed yet ─────────
    # The credit is spent in consume() after the LLM actually runs, so a
    # request rejected below never costs the user a daily optimization.
    rate = get_usage(identifier)
    if not rate.get("allowed", True):
        log.warning(f"[{req_id}] Rate limit hit — identifier: {identifier}")
        err_res = {
            "error":     rate["error"],
            "resets_in": rate["resets_in"],
            "limit":             rate["limit"],
            "queries_used":      rate["queries_used"],
            "queries_remaining": rate["queries_remaining"],
        }
        log.info(f"[{req_id}] /optimize Output: {json.dumps(err_res, default=str)}")
        return JSONResponse(
            status_code=429,
            content=err_res,
        )

    log.info(
        f"[{req_id}] identifier={identifier} — "
        f"{rate['queries_used']}/{rate['limit']} queries used"
    )

    query          = data.get("query", "")
    custom_request = data.get("custom_request", "")

    if not query:
        err_res = {"error": "Query is required."}
        log.info(f"[{req_id}] /optimize Output: {json.dumps(err_res, default=str)}")
        return err_res

    # ── SELECT-only guard (same wall as every other route) ───────────
    # This route previously had NO validation, so a DELETE/UPDATE reached the
    # LLM. Never run a user statement without passing the guard first.
    validation = validate_query(query=query)
    if validation != "FINE":
        log.warning(f"[{req_id}] /optimize rejected query: {validation}")
        err_res = {"error": validation, **rate}
        log.info(f"[{req_id}] /optimize Output: {json.dumps(err_res, default=str)}")
        return err_res

    acquired = await acquire_with_timeout(req_id)
    if not acquired:
        err_res = {"error": "Server is busy. Please try again in a moment."}
        log.info(f"[{req_id}] /optimize Output: {json.dumps(err_res, default=str)}")
        return JSONResponse(
            status_code=503,
            content=err_res
        )

    start = time.time()
    log.info(f"[{req_id}] syntax_optimize started")

    try:
        async def run_syntax_pipeline():
            plan_result     = await syntax_only_planner_llm(query, custom_request)
            raise_on_stage_error(plan_result, "syntax planning stage")

            actions         = plan_result.get("actions", []) if isinstance(plan_result, dict) else []
            # summary         = plan_result.get("summary", "") if isinstance(plan_result, dict) else ""

            await pace_between_stages(req_id, "syntax rewrite stage")

            rewritten       = await syntax_only_rewriter_llm(query, actions)
            raise_on_stage_error(rewritten, "syntax rewrite stage")

            optimized_query = rewritten.get("optimized_query", query) if isinstance(rewritten, dict) else query
            summary = rewritten.get("summary", []) if isinstance(plan_result, dict) else ""

            return {
                "optimized_query":       format_sql_for_display(optimized_query),
                "actions":               actions,
                "index_recommendations": [],
                "summary":               summary,
                "error":                 None,
            }

        result  = await asyncio.wait_for(run_syntax_pipeline(), timeout=LLM_TIMEOUT_SECONDS)
        elapsed = round((time.time() - start) * 1000)
        log.info(f"[{req_id}] syntax_optimize done ({elapsed}ms)")

        # ── LLM actually ran — NOW spend one credit ───────────────────
        rate = consume(identifier)

        # ── Attach usage counters to every successful response ─────────
        result["limit"]             = rate["limit"]
        result["queries_used"]      = rate["queries_used"]
        result["queries_remaining"] = rate["queries_remaining"]

        log.info(f"[{req_id}] /optimize Output: {json.dumps(result, default=str)}")
        return result

    except asyncio.TimeoutError:
        elapsed = round((time.time() - start) * 1000)
        log.error(f"[{req_id}] syntax_optimize timed out ({elapsed}ms)")
        err_res = {"error": "Request timed out. Please try again."}
        log.info(f"[{req_id}] /optimize Output: {json.dumps(err_res, default=str)}")
        return JSONResponse(
            status_code=504,
            content=err_res
        )

    except LLMStageError as e:
        elapsed = round((time.time() - start) * 1000)
        log.error(f"[{req_id}] syntax_optimize LLM stage failed ({elapsed}ms): {e}")
        err_res = {
            "error":     str(e),
            "message":   "The analysis model is temporarily rate limited. Please retry in a few minutes.",
            "retryable": True,
        }
        log.info(f"[{req_id}] /optimize Output: {json.dumps(err_res, default=str)}")
        return JSONResponse(status_code=503, content=err_res)

    except Exception as e:
        elapsed = round((time.time() - start) * 1000)
        log.error(f"[{req_id}] syntax_optimize failed ({elapsed}ms): {e}")
        err_res = {"error": f"Optimization failed: {str(e)}"}
        log.info(f"[{req_id}] /optimize Output: {json.dumps(err_res, default=str)}")
        return err_res

    finally:
        _semaphore.release()


# ══════════════════════════════════════════════════════════════════════
#  R&D: Direct DB connection endpoint (Web App → FastAPI → MySQL)
#  Isolated additive endpoint. Does NOT touch /agent_optimize or /optimize.
#  Security: password is used only for the connection — never logged,
#  never returned, never sent to any LLM.
# ══════════════════════════════════════════════════════════════════════

from pydantic import BaseModel, Field
from db_utils import connect_mysql, connect_db, resolve_engine, redact_config


class DatabaseConfig(BaseModel):
    """Connection details supplied by the web app per-request."""
    host: str = Field(min_length=1)
    port: int = Field(default=3306, ge=1, le=65535)
    user: str = Field(min_length=1)
    password: str = Field(min_length=1)
    database: str = Field(min_length=1)
    # Full TLS cert/identity verification (needs the server CA); off by
    # default so Aiven works without shipping its CA in this R&D phase.
    ssl_verify: bool = False
    # Optional explicit engine: "mysql" | "postgres" (aliases accepted). When
    # omitted the backend probes the wire protocol and uses the engine it
    # finds, so callers that only ever used MySQL are unaffected.
    db_type: str | None = None


def validate_for_engine(query: str, engine: str | None) -> str:
    """
    SELECT-only validation that tolerates the target engine's dialect.

    With an explicit engine, that dialect only is used. When the engine is
    auto-detected, the query is accepted if *either* dialect parses — so
    PostgreSQL-only syntax (e.g. `::` casts) is not rejected by the MySQL
    parser before the probe has even run. The safety rules themselves are
    dialect-independent and still fully apply: exactly one statement, SELECT
    only, no DDL/DML node anywhere in the tree.
    """
    if engine:
        return validate_query(query, dialect=engine)
    first = validate_query(query, dialect="mysql")
    if first == "FINE":
        return first
    return validate_query(query, dialect="postgres")


class DBTestRequest(BaseModel):
    database_config: DatabaseConfig
    # Optional user SELECT to execute after the connection succeeds.
    query: str | None = Field(default=None, max_length=8000)


def _limit_fields(usage: dict) -> dict:
    """
    Uniform daily-limit block included in EVERY /db_optimize_test response —
    success, validation rejection, bad credentials, and server errors — so the
    UI can always render "N of 5 remaining" regardless of the outcome.
    """
    return {
        "limit":             usage["limit"],
        "queries_used":      usage["queries_used"],
        "queries_remaining": usage["queries_remaining"],
        "queries_left":      usage["queries_remaining"],
        "allowed":           usage.get("allowed", True),
        "resets_in":         usage.get("resets_in"),
        "limit_message":     (
            f"{usage['queries_remaining']} of {usage['limit']} optimizations remaining today"
        ),
    }


@app.post("/db_optimize_test")
async def db_optimize_test(req: DBTestRequest, request: Request):
    req_id = str(uuid.uuid4())[:8]
    cfg    = req.database_config.model_dump()
    safe_cfg = redact_config(cfg)
    # Test Connection is a credential check, not an optimization: it reports
    # the current counter but must never consume one of the 5 daily queries.
    usage = get_usage(get_rate_limit_identifier(request))
    limit_info = _limit_fields(usage)
    log.info(f"[{req_id}] /db_optimize_test Input: {json.dumps(safe_cfg)} query={req.query!r}")

    # ── 1. SELECT-only validation (reuse existing sqlglot validator) ──
    if req.query:
        validation = validate_for_engine(req.query, resolve_engine(cfg))
        if validation != "FINE":
            log.info(f"[{req_id}] /db_optimize_test rejected query: {validation}")
            return {"success": False, "error": validation, **limit_info}

    # ── 2. Optional env-var fallbacks for dev testing ─────────────────
    #     Request values always win over DB_* environment variables.
    if not cfg.get("host") or cfg.get("host") == "from-env":
        cfg["host"] = os.getenv("DB_HOST", cfg.get("host") or "")
    if cfg.get("password") in ("", None, "from-env"):
        cfg["password"] = os.getenv("DB_PASSWORD", "")
    if not cfg.get("database") or cfg.get("database") == "from-env":
        cfg["database"] = os.getenv("DB_NAME", cfg.get("database") or "")

    # ── 3. Connect → execute → close (short-lived, per request) ───────
    conn = None
    start = time.time()
    try:
        # allow_read_only=False: Test Connection must verify the credentials the
        # user actually typed. If we swapped in our own read-only account here a
        # wrong password would be reported as a successful connection.
        conn, engine = await asyncio.to_thread(
            connect_db, cfg, req_id, None, False
        )

        result_rows: list | None = None
        executed = "SELECT 1"
        if req.query:
            executed = req.query.strip().rstrip(";")

        def _run():
            cur = conn.cursor()
            try:
                cur.execute(executed)
                # psycopg2 cursors have no `with_rows`; they expose
                # cursor.description for result-returning statements instead.
                # NB: getattr()'s default is evaluated eagerly, so a cursor
                # without `description` would raise before the fallback ran —
                # probe the attribute explicitly instead.
                has_rows = getattr(cur, "with_rows", None)
                if has_rows is None:
                    has_rows = getattr(cur, "description", None) is not None
                rows = cur.fetchall() if has_rows else None
                return [list(map(str, r))[:20] for r in (rows or [])][:50]
            finally:
                cur.close()

        result_rows = await asyncio.to_thread(_run)
        elapsed = round((time.time() - start) * 1000)
        log.info(f"[{req_id}] /db_optimize_test done ({elapsed}ms) db={cfg.get('database')}")
        return {
            "success":          True,
            "message":          "Database connection successful",
            "engine":           engine,
            "database":         cfg.get("database"),
            "executed":         executed,
            "rows":             result_rows,
            "elapsed_ms":       elapsed,
            # Real counters — Test Connection does not consume a query.
            **limit_info,
        }

    except ValueError as e:
        # Safe, curated messages from db_utils (no secrets inside) — they now
        # carry the driver's reason: driver message, errno, sqlstate, elapsed.
        failed_ms = round((time.time() - start) * 1000)
        log.warning(f"[{req_id}] /db_optimize_test failed after {failed_ms}ms: {e}")
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": str(e), **limit_info},
        )
    except Exception as e:
        # The HTTP response stays generic (never leak internals to the
        # caller); the full detail — exception type, message and traceback —
        # goes to the server log only.
        log.exception(
            f"[{req_id}] /db_optimize_test unexpected error: {type(e).__name__}: {e}"
        )
        return JSONResponse(
            status_code=502,
            content={"success": False, "error": "Database connection failed", **limit_info},
        )
    finally:
        if conn is not None:
            try:
                conn.close()
                log.info(f"[{req_id}] /db_optimize_test connection closed")
            except Exception:
                pass



# ══════════════════════════════════════════════════════════════════════
#  R&D Step 2: Combined DB optimization payload (schema + EXPLAIN +
#  diagnostics). LLM optimization is intentionally left as a reserved
#  placeholder for a later step so this milestone stays DB-only.
# ══════════════════════════════════════════════════════════════════════

from db_schema import extract_schema as _extract_schema
from db_schema import detect_static_issues
from db_explain import get_explain_analyze as _get_explain_analyze
from db_explain import run_diagnostics_pipeline as _run_diagnostics_pipeline
# connect_db()/resolve_engine() are imported at module scope above; connect_db()
# returns (connection, engine) and picks the driver by probing the wire protocol.


class DBOptimizeRequest(BaseModel):
    database_config: DatabaseConfig
    query: str = Field(min_length=1, max_length=8000)
    custom_request: str = ""


@app.post("/db_optimize")
async def db_optimize(req: DBOptimizeRequest, request: Request):
    req_id = str(uuid.uuid4())[:8]
    cfg = req.database_config.model_dump()
    log.info(
        f"[{req_id}] /db_optimize Input: {json.dumps(redact_config(cfg))} "
        f"query={req.query!r} custom_request={req.custom_request!r}"
    )

    # ── SELECT-only validation (same guard as everywhere else) ────────
    validation = validate_for_engine(req.query, resolve_engine(cfg))
    if validation != "FINE":
        log.info(f"[{req_id}] /db_optimize rejected query: {validation}")
        return {"success": False, "error": validation}

    conn = None
    engine = None
    start = time.time()
    try:
        conn, engine = await asyncio.to_thread(connect_db, cfg, req_id)

        def _schema():
            db = cfg.get("database") or ""
            return _extract_schema(
                req.query.strip().rstrip(";"),
                conn,
                db,
                engine,
            )

        tables_schema, meta = await asyncio.to_thread(_schema)

        def _diag():
            return _run_diagnostics_pipeline(conn, req.query.strip(), engine)

        diagnostics = await asyncio.to_thread(_diag)
        elapsed = round((time.time() - start) * 1000)

        log.info(
            f"[{req_id}] /db_optimize done ({elapsed}ms) "
            f"tables={list(tables_schema.keys())}"
        )
        return {
            "success": True,
            "database": cfg.get("database"),
            "engine": engine,
            "query_tables": sorted(meta.get("tables", [])),
            "joins": meta.get("joins", []),
            "schema": tables_schema,
            "explain": diagnostics.get("explain"),
            "diagnostics": {
                "warnings": diagnostics.get("warnings"),
                "status": diagnostics.get("status"),
            },
            "optimizer": {
                "status": "pending",
                "message": (
                    "Database read stage complete. "
                    "LLM optimization (issue_finder / planner / rewriter) "
                    "not wired yet."
                ),
            },
            "elapsed_ms": elapsed,
            "custom_request": req.custom_request,
        }

    except ValueError as e:
        log.warning(f"[{req_id}] /db_optimize failed: {e}")
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": str(e)},
        )
    except Exception as e:
        # Full detail (type + message + traceback) in the server log; the HTTP
        # response stays generic so nothing internal leaks to the caller.
        log.exception(
            f"[{req_id}] /db_optimize unexpected error: {type(e).__name__}: {e}"
        )
        return JSONResponse(
            status_code=502,
            content={"success": False, "error": "Database connection failed"},
        )
    finally:
        if conn is not None:
            try:
                conn.close()
                log.info(f"[{req_id}] /db_optimize connection closed")
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════
#  R&D Step 1: Schema extraction over the direct DB connection.
#  Reuses agent_v3.py's schema logic (via db_schema.py, MySQLdb-free copy)
#  so the output shape is exactly what issue_finder_llm() already expects.
# ══════════════════════════════════════════════════════════════════════

from db_schema import extract_schema
from db_schema import resolve_query_tables


class DBSchemaRequest(BaseModel):
    database_config: DatabaseConfig
    # The SQL query whose tables drive the extraction (SELECT-only).
    query: str = Field(min_length=1, max_length=8000)


@app.post("/db_schema_extract")
async def db_schema_extract(req: DBSchemaRequest, request: Request):
    req_id = str(uuid.uuid4())[:8]
    cfg    = req.database_config.model_dump()
    log.info(f"[{req_id}] /db_schema_extract Input: {json.dumps(redact_config(cfg))} query={req.query!r}")

    # ── SELECT-only validation (same guard as everywhere else) ────────
    validation = validate_for_engine(req.query, resolve_engine(cfg))
    if validation != "FINE":
        log.info(f"[{req_id}] /db_schema_extract rejected query: {validation}")
        return {"success": False, "error": validation}

    conn = None
    engine = "mysql"
    start = time.time()
    try:
        conn, engine = await asyncio.to_thread(connect_db, cfg, req_id)

        def _extract():
            return extract_schema(req.query.strip().rstrip(";"), conn,
                                  cfg.get("database") or "", engine)

        tables_schema, meta = await asyncio.to_thread(_extract)

        # -- Which of the query's tables can this connection actually see? --
        # sqlglot reads the query text only, so query_tables can name a table
        # this connection cannot resolve (wrong schema name, identifier case,
        # missing table). extract_schema() mirrors agent_v3 and swallows
        # per-table failures, which turns that into a silent empty schema -
        # so report the reason instead of returning success with no clue.
        def _resolve():
            cursor = conn.cursor()
            try:
                return resolve_query_tables(
                    cursor, meta.get("tables", []), engine)
            finally:
                cursor.close()

        unresolved_tables, resolved_tables = await asyncio.to_thread(_resolve)
        elapsed = round((time.time() - start) * 1000)
        log.info(
            f"[{req_id}] /db_schema_extract done ({elapsed}ms) "
            f"tables={list(tables_schema.keys())} "
            f"unresolved={sorted(unresolved_tables)}"
        )
        for _reason in unresolved_tables.values():
            log.warning(f"[{req_id}] /db_schema_extract {_reason}")

        return {
            "success":           True,
            "engine":            engine,
            "database":          cfg.get("database"),
            "query_tables":      sorted(meta.get("tables", [])),
            "joins":             meta.get("joins", []),
            "schema":            tables_schema,   # same shape issue_finder_llm expects
            "resolved_tables":   resolved_tables,
            "unresolved_tables": unresolved_tables,
            "schema_warnings":   list(unresolved_tables.values()),
            "elapsed_ms":        elapsed,
        }

    except ValueError as e:
        log.warning(f"[{req_id}] /db_schema_extract failed: {e}")
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})
    except Exception as e:
        # Full detail (type + message + traceback) in the server log; the HTTP
        # response stays generic.
        log.exception(
            f"[{req_id}] /db_schema_extract unexpected error: {type(e).__name__}: {e}"
        )
        return JSONResponse(status_code=502, content={"success": False, "error": "Database connection failed"})
    finally:
        if conn is not None:
            try:
                conn.close()
                log.info(f"[{req_id}] /db_schema_extract connection closed")
            except Exception:
                pass

# ══════════════════════════════════════════════════════════════════════
#  R&D Step 2b: Diagnostics-only explain endpoint.
#  Returns the normalized EXPLAIN ANALYZE + SHOW WARNINGS shape from the
#  real remote database, without invoking the LLM optimization pipeline.
# ══════════════════════════════════════════════════════════════════════


class DBExplainRequest(BaseModel):
    database_config: DatabaseConfig
    query: str = Field(min_length=1, max_length=8000)


@app.post("/db_explain_test")
async def db_explain_test(req: DBExplainRequest, request: Request):
    req_id = str(uuid.uuid4())[:8]
    cfg = req.database_config.model_dump()
    safe_cfg = redact_config(cfg)
    # Diagnostics only — reports the counter but consumes no query.
    usage = get_usage(get_rate_limit_identifier(request))
    log.info(f"[{req_id}] /db_explain_test Input: {json.dumps(safe_cfg)} query={req.query!r}")

    # ── 1. SELECT-only validation (same guard as the other DB routes) ──
    validation = validate_for_engine(req.query, resolve_engine(cfg))
    if validation != "FINE":
        log.info(f"[{req_id}] /db_explain_test rejected query: {validation}")
        return {"success": False, "error": validation}

    # ── 2. Optional env-var fallbacks for dev testing ──────────────────
    if not cfg.get("host") or cfg.get("host") == "from-env":
        cfg["host"] = os.getenv("DB_HOST", cfg.get("host") or "")
    if cfg.get("password") in ("", None, "from-env"):
        cfg["password"] = os.getenv("DB_PASSWORD", "")
    if not cfg.get("database") or cfg.get("database") == "from-env":
        cfg["database"] = os.getenv("DB_NAME", cfg.get("database") or "")

    conn = None
    engine = "mysql"
    start = time.time()
    try:
        conn, engine = await asyncio.to_thread(connect_db, cfg, req_id)
        elapsed = round((time.time() - start) * 1000)

        diag = _run_diagnostics_pipeline(conn, req.query, engine)
        log.info(f"[{req_id}] /db_explain_test diag keys: {list(diag.keys())}")

        explain = diag.get("explain") or []
        warnings = diag.get("warnings") or {}

        return {
            "success": True,
            "message": "EXPLAIN ANALYZE collected",
            "engine": engine,
            "database": cfg.get("database"),
            "query": req.query,
            "explain": explain,
            "warnings": warnings,
            "elapsed_ms": elapsed,
            # Diagnostics only — no optimization performed, no query consumed.
            "limit":             usage["limit"],
            "queries_used":      usage["queries_used"],
            "queries_remaining": usage["queries_remaining"],
        }

    except ValueError as e:
        log.warning(f"[{req_id}] /db_explain_test failed: {e}")
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})
    except Exception as e:
        # Full detail (type + message + traceback) in the server log; the HTTP
        # response stays generic.
        log.exception(
            f"[{req_id}] /db_explain_test unexpected error: {type(e).__name__}: {e}"
        )
        return JSONResponse(
            status_code=502,
            content={"success": False, "error": "Database connection failed"},
        )
    finally:
        if conn is not None:
            try:
                conn.close()
                log.info(f"[{req_id}] /db_explain_test connection closed")
            except Exception:
                pass

# ══════════════════════════════════════════════════════════════════════
#  R&D Step 3: Full DB-backed optimization.
#  Schema + EXPLAIN diagnostics from the supplied remote DB are fed into the
#  existing issue_finder -> planner -> rewriter pipeline. DB credentials are
#  per-request and never exposed in logs or responses.
# ══════════════════════════════════════════════════════════════════════


class DBOptimizeFullRequest(BaseModel):
    database_config: DatabaseConfig
    query: str = Field(min_length=1, max_length=8000)
    custom_request: str = ""
    scope: list[str] = Field(default_factory=list)


@app.post("/db_optimize_full")
async def db_optimize_full(req: DBOptimizeFullRequest, request: Request):
    req_id = str(uuid.uuid4())[:8]
    cfg = req.database_config.model_dump()
    safe_cfg = redact_config(cfg)
    log.info(
        f"[{req_id}] /db_optimize_full Input: {json.dumps(safe_cfg)} "
        f"query={req.query!r} scope={req.scope!r} custom_request={req.custom_request!r}"
    )

    # ── 1. SELECT-only gate (same guard as the other DB routes) ────
    validation = validate_for_engine(req.query, resolve_engine(cfg))
    if validation != "FINE":
        log.info(f"[{req_id}] /db_optimize_full rejected query: {validation}")
        return {"success": False, "error": validation}

    # ── 1b. Rate limit gate — CHECK ONLY, nothing consumed yet ────────
    # This route runs the full issue_finder -> planner -> rewriter pipeline,
    # so it spends one credit — but only after the LLM actually runs (below).
    identifier = get_rate_limit_identifier(request)
    usage = get_usage(identifier)
    if not usage.get("allowed", True):
        log.warning(f"[{req_id}] Rate limit hit — identifier: {identifier}")
        return JSONResponse(
            status_code=429,
            content={
                "success":           False,
                "error":             usage["error"],
                "resets_in":         usage["resets_in"],
                "limit":             usage["limit"],
                "queries_used":      usage["queries_used"],
                "queries_remaining": usage["queries_remaining"],
            },
        )
    log.info(
        f"[{req_id}] identifier={identifier} — "
        f"{usage['queries_used']}/{usage['limit']} queries used"
    )

    # ── 2. Optional env-var fallbacks for dev testing ──────────────
    if not cfg.get("host") or cfg.get("host") == "from-env":
        cfg["host"] = os.getenv("DB_HOST", cfg.get("host") or "")
    if cfg.get("password") in ("", None, "from-env"):
        cfg["password"] = os.getenv("DB_PASSWORD", "")
    if not cfg.get("database") or cfg.get("database") == "from-env":
        cfg["database"] = os.getenv("DB_NAME", cfg.get("database") or "")

    conn = None
    engine = "mysql"
    start = time.time()
    try:
        conn, engine = await asyncio.to_thread(connect_db, cfg, req_id)

        def _schema():
            db = cfg.get("database") or ""
            return _extract_schema(
                req.query.strip().rstrip(";"),
                conn,
                db,
                engine,
            )

        tables_schema, meta = await asyncio.to_thread(_schema)

        # Columns the query references but the database does not have —
        # the query would fail if executed and EXPLAIN ANALYZE returns
        # nothing, so surface the mismatch instead of staying silent.
        # A table the connection cannot see yields an empty schema, in which
        # case every referenced column looks "missing" and the real problem
        # (wrong schema name / identifier case) would be misreported. Detect
        # that first, then check columns only on tables the catalog confirms.
        def _resolve():
            cursor = conn.cursor()
            try:
                return resolve_query_tables(
                    cursor, meta.get("tables", []), engine)
            finally:
                cursor.close()

        unresolved_tables, _resolved_tables = await asyncio.to_thread(_resolve)

        schema_warnings = []
        for _table, _reason in sorted(unresolved_tables.items()):
            schema_warnings.append(_reason)
            log.warning(f"[{req_id}] /db_optimize_full {_reason}")

        for _table in sorted(meta.get("tables", set())):
            if _table in unresolved_tables:
                continue
            _referenced = set()
            for _key in ("where", "join", "order", "group", "select"):
                _referenced |= meta.get(_key, {}).get(_table, set())
            _present = {
                c.get("name")
                for c in tables_schema.get(_table, {}).get("columns", [])
            }
            _missing = sorted(_referenced - _present)
            if _missing:
                _msg = f"Query references missing column(s) on {_table}: {', '.join(_missing)}"
                schema_warnings.append(_msg)
                log.warning(f"[{req_id}] /db_optimize_full {_msg}")

        def _diag():
            # get_explain_analyze() rejects any statement that still has ";",
            # so strip the terminator users normally type (matches _schema).
            return _run_diagnostics_pipeline(conn, req.query.strip().rstrip(";"), engine)

        diagnostics = await asyncio.to_thread(_diag)

        # ── 3. Three-stage LLM pipeline — same calling convention as
        #      /agent_optimize (issue_finder -> planner -> rewriter) ─────
        acquired = await acquire_with_timeout(req_id)
        if not acquired:
            log.warning(f"[{req_id}] /db_optimize_full busy — no pipeline slot")
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "error": "Server is busy. Please try again in a moment.",
                },
            )

        # run_diagnostics_pipeline() output is fed to the analyst using the
        # key names the agent path sends: EXPLAIN rows -> `explain`,
        # SHOW WARNINGS -> optimizer_warnings, SHOW SESSION STATUS ->
        # handler_stats.
        combined_analysis = {
            "optimizer_warnings": diagnostics.get("warnings"),
            "handler_stats": diagnostics.get("status"),
        }
        if schema_warnings:
            # Tell the analyst which referenced columns do not exist so it
            # stops reporting issues / CREATE INDEX actions on them.
            combined_analysis["schema_mismatches"] = schema_warnings

        async def run_pipeline():
            issues_result = await issue_finder_llm(
                req.query,
                diagnostics.get("explain") or {},
                combined_analysis,
                req.scope,
                tables_schema,
                req.custom_request,
            )
            raise_on_stage_error(issues_result, "issue analysis stage")

            issues = issues_result.get("issues", []) if isinstance(issues_result, dict) else []

            # ── Deterministic findings the model is not asked to make ────
            # Under the joins/indexes scopes the issue-finder is explicitly told
            # not to discuss generic SQL structure, so a provably-broken
            # construct can come back as zero issues — observed on
            # ORDER BY (SELECT RANDOM()), which never sorts at all. These rules
            # always run: the findings are reported in the response regardless
            # of what the planner does, and are merged into `issues` so the
            # planner and rewriter can act on them.
            static_findings = detect_static_issues(
                req.query, "postgres" if engine == "postgres" else "mysql"
            )
            if static_findings:
                haystack = " ".join(
                    f"{i.get('title', '')} {i.get('location', '')}"
                    for i in issues if isinstance(i, dict)
                ).lower()
                next_id = max(
                    [i.get("id") or 0 for i in issues if isinstance(i, dict)],
                    default=0,
                ) + 1
                merged = 0
                for finding in static_findings:
                    # `rule` is the internal dedupe probe, not planner input:
                    # if the model already covered this topic, don't double it.
                    probe = finding.pop("rule", "")
                    if probe and probe in haystack:
                        log.info(
                            f"[{req_id}] static rule already covered by model: {probe!r}"
                        )
                        continue
                    finding["id"] = next_id
                    next_id += 1
                    issues.append(finding)
                    merged += 1
                log.info(
                    f"[{req_id}] static findings: {len(static_findings)} "
                    f"({merged} merged into issues)"
                )

            log.info(f"[{req_id}] issue analysis stage: issues={len(issues)}")

            if not issues:
                # Nothing to act on — planning and rewriting would spend two
                # more LLM calls to re-derive an empty result.
                log.info(f"[{req_id}] no issues — skipping planner and rewriter")
                return {
                    "actions": [],
                    "optimized_query": req.query,
                    "index_recommendations": [],
                    "summary": ["No optimizations were applied to the query."],
                    "applied_actions": [],
                    "skipped_actions": [],
                    "rewriter_warnings": [],
                    "static_findings": static_findings,
                }

            await pace_between_stages(req_id, "planning stage")

            action_result = await planner_llm(
                req.query, issues, tables_schema, req.scope, req.custom_request,
            )
            raise_on_stage_error(action_result, "planning stage")

            actions = action_result.get("actions", []) if isinstance(action_result, dict) else []
            execution_order = action_result.get("execution_order", []) if isinstance(action_result, dict) else []
            if actions and not execution_order:
                # A planner omission must not silently disable the rewriter:
                # with an empty order it treats the plan as "nothing to
                # apply" and returns the original query. Fall back to the
                # planner's own action order instead.
                execution_order = [a.get("id") for a in actions if isinstance(a, dict)]
                log.warning(
                    f"[{req_id}] planner returned no execution_order — "
                    f"derived from action ids: {execution_order}"
                )
            log.info(
                f"[{req_id}] planner stage: actions={len(actions)} "
                f"execution_order={execution_order}"
            )
            await pace_between_stages(req_id, "rewrite stage")

            rewriter_result = await rewriter_llm(
                req.query, tables_schema, actions,
                req.custom_request, req.scope, execution_order,
            )
            raise_on_stage_error(rewriter_result, "rewrite stage")

            _opt = rewriter_result.get("optimized_query") if isinstance(rewriter_result, dict) else None
            log.info(
                f"[{req_id}] rewriter stage: "
                f"keys={sorted(rewriter_result) if isinstance(rewriter_result, dict) else type(rewriter_result).__name__} "
                f"optimized_changed={bool(_opt and _opt.strip().rstrip(';') != req.query.strip().rstrip(';'))}"
            )

            return {
                "actions": actions,
                "optimized_query": rewriter_result.get("optimized_query", req.query)
                if isinstance(rewriter_result, dict) else req.query,
                "index_recommendations": rewriter_result.get("ddl_statements", [])
                if isinstance(rewriter_result, dict) else [],
                "summary": rewriter_result.get("summary", [])
                if isinstance(rewriter_result, dict) else [],
                "applied_actions": rewriter_result.get("applied_actions", [])
                if isinstance(rewriter_result, dict) else [],
                "skipped_actions": rewriter_result.get("skipped_actions", [])
                if isinstance(rewriter_result, dict) else [],
                "rewriter_warnings": rewriter_result.get("warnings", [])
                if isinstance(rewriter_result, dict) else [],
                "static_findings": static_findings,
            }

        try:
            stages = await asyncio.wait_for(run_pipeline(), timeout=LLM_TIMEOUT_SECONDS)
        finally:
            _semaphore.release()

        # Recomputed below: the optimized-query EXPLAIN runs after this point,
        # and the report's "Duration" must reflect the whole request, not just
        # the LLM pipeline.
        elapsed = round((time.time() - start) * 1000)
        log.info(f"[{req_id}] /db_optimize_full pipeline complete ({elapsed}ms)")

        # ── Make the applied edits visible ────────────────────────────
        # optimized_query is reindented, so a small deletion inside a long
        # WHERE clause is nearly invisible beside the original — a successful
        # rewrite reads like "the same query". Report the real diff, and keep
        # acknowledged NO_OP / maintenance actions out of the edit count:
        # they land in applied_actions but by definition change no SQL.
        query_changed, sql_changes = diff_sql(req.query, stages["optimized_query"])

        _action_by_id = {
            a.get("id"): a for a in stages["actions"] if isinstance(a, dict)
        }
        no_op_actions = [
            aid for aid in stages["applied_actions"]
            if (_action_by_id.get(aid) or {}).get("type") in NON_SQL_CHANGING_ACTIONS
        ]
        rewrite_actions = [
            aid for aid in stages["applied_actions"] if aid not in no_op_actions
        ]

        if not query_changed and stages["applied_actions"]:
            log.warning(
                f"[{req_id}] /db_optimize_full rewriter reported applied_actions="
                f"{stages['applied_actions']} but optimized_query is unchanged "
                f"(rewrite actions={rewrite_actions or 'none'}, "
                f"no-ops={no_op_actions or 'none'}) — nothing was rewritten"
            )
        log.info(
            f"[{req_id}] /db_optimize_full changes: query_changed={query_changed} "
            f"rewrite_actions={rewrite_actions} no_op_actions={no_op_actions} "
            f"changes={sql_changes}"
        )

        # ── EXPLAIN ANALYZE the REWRITTEN query ──────────────────────
        # `explain` above is only the ORIGINAL plan, so the UI can show what
        # the query did before but never what it does after the rewrite — the
        # one number that proves the optimization worked. Re-plan only when the
        # SQL actually changed (an unchanged query has the same plan by
        # definition, and EXPLAIN ANALYZE is not free).
        #
        # Strictly best-effort: the rewriter can emit SQL that EXPLAIN rejects
        # (unsupported syntax, a window function the engine disallows), and a
        # missing after-plan must never fail an otherwise successful
        # optimization. get_explain_analyze() already returns None on driver
        # errors; this guards the remaining failure modes — a closed/invalid
        # connection or a malformed statement reaching the driver.
        explain_optimized = None
        explain_optimized_summary = None
        if query_changed:
            def _diag_optimized():
                return _get_explain_analyze(
                    conn,
                    stages["optimized_query"].strip().rstrip(";"),
                    engine,
                )

            try:
                explain_optimized = await asyncio.to_thread(_diag_optimized)
            except Exception as e:
                log.warning(
                    f"[{req_id}] /db_optimize_full EXPLAIN ANALYZE of the "
                    f"optimized query failed: {type(e).__name__}: {e}"
                )
            else:
                log.info(
                    f"[{req_id}] /db_optimize_full optimized-query plan: "
                    f"{'collected' if explain_optimized else 'unavailable'}"
                )

            # ── Describe the REWRITTEN plan in plain English ───────────
            # Strictly sequential: this cannot start until the rewriter has
            # produced the optimized SQL and that SQL has been re-planned, so
            # there is nothing to summarize before the block above finishes.
            #
            # Scoped to the optimized query only — the original plan is
            # already explained by issue_finder, and comparing the two is the
            # planner's job, not this stage's.
            #
            # Best-effort like every other optional field: a summary is a
            # nicety, so a timeout, a rate limit, or malformed model output
            # degrades to "no summary" and must never fail the optimization.
            if explain_optimized:
                try:
                    explain_optimized_summary = await explain_summarizer_llm(
                        stages["optimized_query"], explain_optimized
                    )
                except Exception as e:
                    log.warning(
                        f"[{req_id}] /db_optimize_full explain summarizer "
                        f"failed: {type(e).__name__}: {e}"
                    )
                    explain_optimized_summary = None
                else:
                    log.info(
                        f"[{req_id}] /db_optimize_full optimized-query summary: "
                        f"{'ready' if explain_optimized_summary else 'unavailable'}"
                    )

        # ── LLM pipeline actually ran — NOW spend one credit ───────────
        rate = consume(identifier)

        # Re-measure so the reported Duration includes the optimized-query
        # EXPLAIN ANALYZE above, not just the LLM pipeline.
        elapsed = round((time.time() - start) * 1000)

        return {
            "success": True,
            "message": "Query optimized using live database diagnostics",
            "engine": engine,
            "database": cfg.get("database"),
            "query": req.query,
            "optimized_query": format_sql_for_display(stages["optimized_query"]),
            "query_changed": query_changed,
            "changes": sql_changes,
            "actions": stages["actions"],
            "index_recommendations": stages["index_recommendations"],
            "summary": stages["summary"],
            "applied_actions": stages["applied_actions"],
            "rewrite_actions": rewrite_actions,
            "no_op_actions": no_op_actions,
            "skipped_actions": stages["skipped_actions"],
            "rewriter_warnings": stages["rewriter_warnings"],
            "explain": diagnostics.get("explain"),
            "explain_optimized": explain_optimized,
            "explain_optimized_summary": explain_optimized_summary or None,
            "schema_used": tables_schema,
            "schema_warnings": schema_warnings,
            "static_findings": stages.get("static_findings", []),
            "limit":             rate["limit"],
            "queries_used":      rate["queries_used"],
            "queries_remaining": rate["queries_remaining"],
            "elapsed_ms": elapsed,
        }

    except ValueError as e:
        log.warning(f"[{req_id}] /db_optimize_full failed: {e}")
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})
    except LLMStageError as e:
        log.error(f"[{req_id}] /db_optimize_full LLM stage failed: {e}")
        return JSONResponse(status_code=502, content={"success": False, "error": str(e)})
    except asyncio.TimeoutError:
        log.exception(f"[{req_id}] /db_optimize_full timed out after {LLM_TIMEOUT_SECONDS}s")
        return JSONResponse(
            status_code=504,
            content={"success": False, "error": "Optimization timed out"},
        )
    except Exception as e:
        log.exception(f"[{req_id}] /db_optimize_full unexpected error: {type(e).__name__}: {e}")
        return JSONResponse(
            status_code=502,
            content={"success": False, "error": "Database connection failed"},
        )
    finally:
        if conn is not None:
            try:
                conn.close()
                log.info(f"[{req_id}] /db_optimize_full connection closed")
            except Exception:
                pass

