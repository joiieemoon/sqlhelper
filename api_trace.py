"""
api_trace.py
------------
Structured API trace log — one JSON object per request, appended to a file.

This is the developer/monitoring counterpart to the human-readable
`log/api.log`. It answers the questions a text log cannot:

  * exactly what query went in, and what came back
  * how long the call really took
  * whether the optimizer actually changed the query
  * how accurate the result was (issues, actions, index recommendations)
  * whether the daily quota was consumed
  * which device sent it

Design rules:
  * ONE JSON PER LINE (JSONL) so it can be tailed, grepped and streamed.
  * NEVER record a password. `_redact` scrubs database_config.password and any
    other credential-looking key, in both request and response.
  * Writing must never break a request: every I/O path is guarded.
  * Bounded: oversized bodies are truncated with an explicit marker so one
    EXPLAIN dump cannot fill the disk.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any

# ── Config ────────────────────────────────────────────────────────────

TRACE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "trace"
)
TRACE_FILE = os.path.join(TRACE_DIR, "api_trace.jsonl")

MAX_FIELD_CHARS = 20000    # per field
MAX_BODY_CHARS = 60000     # whole response summary

_SECRET_KEY_RE = re.compile(
    r"(pass(word|wd)?|secret|token|api[_-]?key|credential|authorization|"
    r"private[_-]?key|session[_-]?key)",
    re.IGNORECASE,
)
_REDACTED = "***REDACTED***"

_lock = threading.Lock()
_warned = False


def _ensure_dir() -> bool:
    global _warned
    try:
        os.makedirs(TRACE_DIR, exist_ok=True)
        return True
    except Exception as exc:  # noqa: BLE001
        if not _warned:
            _warned = True
            print(f"[api_trace] cannot create {TRACE_DIR}: {exc}")
        return False


def _truncate(value: Any, limit: int = MAX_FIELD_CHARS) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"\n...[truncated, {len(value)} chars total]"
    return value


def _redact(obj: Any, depth: int = 0) -> Any:
    """Recursively replace secret-looking values. Depth-limited."""
    if depth > 12:
        return _truncate(obj)
    if isinstance(obj, dict):
        out = {}
        for key, val in obj.items():
            if isinstance(key, str) and _SECRET_KEY_RE.search(key):
                out[key] = _REDACTED
            else:
                out[key] = _redact(val, depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [_redact(v, depth + 1) for v in obj]
    return _truncate(obj)


def _compact_explain(explain: Any) -> Any:
    """Reduce an explain payload to table/access-type/key/rows per row."""
    if isinstance(explain, dict):
        rows = explain.get("rows")
        return _compact_explain(rows) if rows else _truncate(explain)
    if not isinstance(explain, list):
        return _truncate(explain)

    out = []
    for row in explain[:20]:
        if not isinstance(row, dict):
            continue
        out.append({
            k: row.get(k)
            for k in ("table", "type", "possible_keys", "key", "rows",
                      "filtered", "extra", "actual_rows", "estimated_rows")
            if k in row
        })
    if len(explain) > 20:
        out.append({"...": f"{len(explain) - 20} more plan rows omitted"})
    return out


def _compact_schema(schema: Any) -> Any:
    """Reduce a schema payload to per-table row count + index names."""
    if not isinstance(schema, dict):
        return _truncate(schema)
    out = {}
    for table, meta in list(schema.items())[:30]:
        if isinstance(meta, dict):
            out[table] = {
                "row_count": meta.get("row_count"),
                "columns": len(meta.get("columns") or []),
                "indexes": [
                    (i.get("name") if isinstance(i, dict) else str(i))
                    for i in (meta.get("indexes") or [])
                ],
            }
        else:
            out[table] = _truncate(meta)
    return out


# Fields kept verbatim in the trace — everything a reviewer needs to judge
# whether the optimizer was right and how much it changed.
_KEEP_FIELDS = (
    "success", "message", "error", "detail", "engine", "database",
    "query", "executed", "rows", "query_tables", "joins",
    "optimized_query", "query_changed", "changes",
    "actions", "index_recommendations", "summary",
    "applied_actions", "rewrite_actions", "no_op_actions",
    "skipped_actions", "rewriter_warnings", "static_findings",
    "schema_warnings", "warnings", "explain_optimized_summary",
    "limit", "queries_used", "queries_remaining", "queries_left",
    "allowed", "resets_in", "limit_message", "elapsed_ms",
)


def _summarise_response(body: Any) -> Any:
    """
    Keep the reviewable fields; compact the heavy diagnostics.

    EXPLAIN and schema payloads are the largest and the least useful in full
    when scanning many runs, so they are reduced to a compact shape.
    """
    if not isinstance(body, dict):
        return _truncate(body, MAX_BODY_CHARS)

    keep = {k: _redact(body[k]) for k in _KEEP_FIELDS if k in body}

    if "explain" in body:
        keep["explain"] = _compact_explain(body["explain"])
    if "explain_optimized" in body:
        keep["explain_optimized"] = _compact_explain(body["explain_optimized"])
    if "schema_used" in body:
        keep["schema_used"] = _compact_schema(body["schema_used"])

    if len(json.dumps(keep, default=str)) > MAX_BODY_CHARS:
        return _truncate(keep, MAX_BODY_CHARS)
    return keep


def _classify(status: int, body: Any) -> str:
    """Coarse outcome label for filtering and dashboards."""
    err = ""
    if isinstance(body, dict):
        err = str(body.get("error") or "")
    if status == 429:
        return "quota_exceeded"
    if status >= 500:
        return "server_error"
    if status == 400:
        return "bad_request"
    if err:
        if "Only read-only" in err or "SELECT" in err or "keyword" in err:
            return "blocked_by_validator"
        if "password" in err.lower() or "Access denied" in err:
            return "auth_failed"
        if "resolve" in err.lower() or "DNS" in err:
            return "dns_failed"
        return "rejected"
    if status == 200:
        return "ok"
    return "other"


def record(
    *,
    req_id: str,
    method: str,
    path: str,
    status: int,
    elapsed_ms: int,
    request_body: Any = None,
    response_body: Any = None,
    device_id: str = "",
    client: str = "",
    error: str = "",
) -> None:
    """
    Append one trace record. Never raises — tracing must not break a request.
    """
    try:
        if not _ensure_dir():
            return

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "req_id": req_id,
            "method": method,
            "path": path,
            "status": status,
            "elapsed_ms": elapsed_ms,
            "outcome": _classify(status, response_body),
            "device_id": device_id or None,
            "client": client or None,
        }

        if request_body is not None:
            entry["request"] = _redact(request_body)
        if response_body is not None:
            entry["response"] = _summarise_response(response_body)
        if error:
            entry["exception"] = _truncate(error, 2000)

        # Promote the fields a reviewer filters on to the top level.
        if isinstance(request_body, dict):
            entry["query"] = _truncate(request_body.get("query"), 8000)
            entry["scope"] = request_body.get("scope")
            entry["custom_request"] = _truncate(
                request_body.get("custom_request"), 2000
            )
            cfg = request_body.get("database_config")
            if isinstance(cfg, dict):
                entry["db"] = {
                    "host": cfg.get("host"),
                    "port": cfg.get("port"),
                    "user": cfg.get("user"),
                    "database": cfg.get("database"),
                    "password": _REDACTED,   # never the real value
                }

        if isinstance(response_body, dict):
            for key in ("limit", "queries_used", "queries_remaining"):
                if key in response_body:
                    entry[key] = response_body[key]

        line = json.dumps(entry, default=str)

        with _lock:
            with open(TRACE_FILE, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:  # noqa: BLE001 - tracing is best-effort, never fatal
        pass
