# -*- coding: utf-8 -*-
"""
db_explain.py — EXPLAIN ANALYZE and database-side diagnostics for the new
direct-DB optimization backend path.

Supports both engines behind an `engine` argument (default "mysql" so existing
callers are unaffected):

  MySQL       EXPLAIN ANALYZE <query>                      (text plan rows)
              SHOW WARNINGS / SHOW SESSION STATUS
              INFORMATION_SCHEMA.TABLES / .STATISTICS
  PostgreSQL  EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) <q>   (JSON plan tree)
              pg_stat_database + planner GUCs  (the SHOW SESSION STATUS analogue)
              pg_class / pg_index             (via db_schema.py)

This is the backend counterpart to the diagnostics logic previously embedded
in the local Chrome agent (agent_v3.py). It is implemented separately so the
server-side FastAPI path stays self-contained and does not depend on the
local-agent code that cannot be imported on the server.

Read-only by construction:
  - EXPLAIN ANALYZE (reads the optimizer plan / runtime stats)
  - INFORMATION_SCHEMA / pg_catalog reads for table/index/column diagnostics
  - SHOW / pg_settings reads that are read-only session-state reads

NOTE: PostgreSQL has no SHOW WARNINGS equivalent, so that key stays empty for
PostgreSQL. Nothing downstream treats a missing warning list as an error.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

from db_utils import redact_config

log = logging.getLogger("query_optimizer.db.explain")


def _truncate_sql_for_log(sql, params=None):
    """Return a log-safe representation of a query (never the password)."""
    if params:
        try:
            sql = sql % params
        except Exception:
            sql = f"{sql}  /* params omitted from log */"
    if len(sql) > 500:
        return sql[:500] + "..."
    return sql


def _describe_row_description(cursor):
    """Return column descriptions from cursor.description if available."""
    desc = getattr(cursor, "description", None)
    if not desc:
        return []
    return [str(d[0]) for d in desc]


def _execute(conn, sql, params=None):
    """Run a single read-only statement and return the cursor."""
    log.debug("DB exec: %s", _truncate_sql_for_log(sql, params))
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
    except Exception:
        cur.close()
        raise
    return cur


_PG_SUMMARY_FIELDS = {
    "planning_time_ms":       "Planning Time",
    "execution_time_ms":      "Execution Time",
    "top_node_type":          "Node Type",
    "total_cost":             "Total Cost",
    "plan_rows":              "Plan Rows",
    "actual_rows":            "Actual Rows",
    "actual_total_time_ms":   "Actual Total Time",
    "shared_hit_blocks":      "Shared Hit Blocks",
    "shared_read_blocks":     "Shared Read Blocks",
    "sort_method":            "Sort Method",
    "temp_read_blocks":       "Temp Read Blocks",
    "temp_written_blocks":    "Temp Written Blocks",
    "rows_removed_by_filter": "Rows Removed by Filter",
}


def _pg_plan_summary(plan_root: Any) -> dict:
    """
    Flatten the interesting top-level numbers out of a PostgreSQL JSON plan.

    The plan is a nested tree; the analyst prompt only needs the costly figures
    (times, row counts, I/O blocks, sort method), so they are pulled up to a
    flat dict instead of forcing a tree walk inside the LLM prompt.
    """
    if not isinstance(plan_root, dict):
        return {}
    node = plan_root.get("Plan") or {}
    out: Dict[str, Any] = {}
    for label, source in _PG_SUMMARY_FIELDS.items():
        if source in plan_root:
            out[label] = plan_root[source]
        elif source in node:
            out[label] = node[source]
    children = node.get("Plans")
    if isinstance(children, list):
        out["child_node_types"] = [
            c.get("Node Type") for c in children if isinstance(c, dict)
        ]
    return out


def get_explain_analyze(connection, query, engine: str = "mysql"):
    """
    Run EXPLAIN ANALYZE for `query` and return a compact JSON-safe shape.

    Read-only diagnostic; must be called only after SELECT-only validation.
    The query is embedded as literal SQL, not as a bound parameter, because
    both engines take the statement to explain as text.

    MySQL       -> {"rows": [{col: val}, ...], "columns": [...]}
    PostgreSQL  -> the same two keys, plus "plan" (the JSON tree) and
                   "summary" (the flattened top-level stats), so the analyst
                   sees the costly numbers without walking the tree.
    """
    # Defense in depth: reject any remaining statement-boundary characters
    # even though validate_query already enforces a single SELECT AST.
    if ";" in query:
        log.warning("Rejected query with statement terminator for EXPLAIN: %s", query[:200])
        return None

    is_pg = engine == "postgres"
    stmt = (
        f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {query}"
        if is_pg else f"EXPLAIN ANALYZE {query}"
    )
    try:
        cur = _execute(connection, stmt)
        try:
            rows = cur.fetchall()
            cols = _describe_row_description(cur)
        finally:
            cur.close()
    except Exception:
        log.exception("EXPLAIN ANALYZE failed for query")
        return None

    if not is_pg:
        return {"rows": [dict(zip(cols, row)) for row in rows], "columns": cols}

    # PostgreSQL returns exactly one row holding the entire JSON plan. psycopg2
    # normally deserialises it into Python objects, but a raw string is possible
    # depending on the active type adapters, so handle both.
    plan = rows[0][0] if rows else None
    if isinstance(plan, (str, bytes)):
        try:
            plan = json.loads(plan)
        except (TypeError, ValueError):
            log.debug("PostgreSQL EXPLAIN JSON could not be parsed")
            return None
    if isinstance(plan, (list, tuple)):
        plan = plan[0] if plan else None
    if plan is None:
        return None

    return {
        "engine": "postgres",
        "format": "json",
        "plan": plan,
        "summary": _pg_plan_summary(plan),
        # Mirrored flat shape so any consumer that merely renders rows still works.
        "rows": [{"QUERY PLAN": json.dumps(plan, default=str)}],
        "columns": ["QUERY PLAN"],
    }


def get_warnings(connection, engine: str = "mysql"):
    """
    Read SHOW WARNINGS after EXPLAIN ANALYZE (MySQL only).

    PostgreSQL has no equivalent statement — planner notes arrive on the client
    notice channel rather than as a queryable result set — so an empty dict is
    returned and the analyst simply receives no warning list.
    """
    if engine == "postgres":
        return {}
    try:
        cur = _execute(connection, "SHOW WARNINGS")
        try:
            rows = cur.fetchall()
            cols = _describe_row_description(cur)
            return {"rows": [dict(zip(cols, row)) for row in rows], "columns": cols}
        finally:
            cur.close()
    except Exception:
        log.debug("SHOW WARNINGS failed")
        return {}


# Planner knobs that decide which plan shapes PostgreSQL can even consider.
_PG_SESSION_GUCS = (
    "work_mem",
    "random_page_cost",
    "seq_page_cost",
    "effective_cache_size",
    "enable_seqscan",
    "enable_nestloop",
    "enable_hashjoin",
    "enable_sort",
    "max_parallel_workers_per_gather",
    "jit",
)


def _pg_session_status(connection) -> dict:
    """
    PostgreSQL analogue of SHOW SESSION STATUS.

    MySQL reports per-session counters (Handler_read_*, Created_tmp_*).
    PostgreSQL keeps those database-wide in pg_stat_database and exposes the
    planner knobs in pg_settings, so both are collected: the counters show what
    the engine actually did, and the GUCs show which plan shapes this server is
    even willing to choose. Counter keys deliberately echo the MySQL names they
    stand in for, so the analyst prompt keeps reading the same concepts.
    """
    out: Dict[str, Any] = {"Engine": "postgres"}

    try:
        cur = _execute(
            connection,
            """
            SELECT sum(temp_files), sum(temp_bytes), sum(deadlocks),
                   sum(blks_hit), sum(blks_read)
            FROM pg_stat_database
            WHERE datname = current_database()
            """,
        )
        try:
            row = cur.fetchone()
        finally:
            cur.close()
        if row:
            for key, value in zip(
                ("Temp_files", "Temp_bytes", "Deadlocks", "Blks_hit", "Blks_read"), row
            ):
                out[key] = int(value) if value is not None else None
    except Exception:
        log.debug("pg_stat_database read failed")

    try:
        placeholders = ",".join(["%s"] * len(_PG_SESSION_GUCS))
        cur = _execute(
            connection,
            f"SELECT name, setting FROM pg_settings WHERE name IN ({placeholders})",
            list(_PG_SESSION_GUCS),
        )
        try:
            for name, setting in cur.fetchall():
                out[str(name)] = setting
        finally:
            cur.close()
    except Exception:
        log.debug("pg_settings read failed")

    return out


def get_session_status(connection, engine: str = "mysql"):
    """Read session/engine state: SHOW SESSION STATUS or its PostgreSQL analogue."""
    if engine == "postgres":
        return _pg_session_status(connection)
    try:
        cur = _execute(connection, "SHOW SESSION STATUS")
        try:
            out = {}
            for row in cur.fetchall():
                if len(row) >= 2:
                    out[str(row[0])] = row[1]
            return out
        finally:
            cur.close()
    except Exception:
        log.debug("SHOW SESSION STATUS failed")
        return {}


_PG_DIAG_ROWCOUNT_SQL = """
SELECT c.reltuples::bigint
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relname = %s AND n.nspname = current_schema()
"""

_PG_DIAG_INDEX_SQL = """
SELECT CASE WHEN ix.indisprimary THEN 'PRIMARY' ELSE i.relname END,
       a.attname
FROM pg_index ix
JOIN pg_class i      ON i.oid = ix.indexrelid
JOIN pg_class t      ON t.oid = ix.indrelid
JOIN pg_namespace n  ON n.oid = t.relnamespace
JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
JOIN pg_attribute a  ON a.attrelid = t.oid AND a.attnum = k.attnum
WHERE t.relname = %s AND n.nspname = current_schema() AND a.attnum > 0
ORDER BY 1, k.ord
"""


def get_schema_diagnostic(connection, table, engine: str = "mysql"):
    """Compact table-level diagnostic: row estimate + existing indexes."""
    if engine == "postgres":
        return _pg_schema_diagnostic(connection, table)

    row_count = None
    try:
        cur = _execute(
            connection,
            """
            SELECT TABLE_ROWS
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
            """,
            (table,),
        )
        try:
            row = cur.fetchone()
            row_count = row[0] if row else None
        finally:
            cur.close()
    except Exception:
        row_count = None

    indexes = []
    try:
        cur = _execute(
            connection,
            """
            SELECT INDEX_NAME, COLUMN_NAME
            FROM INFORMATION_SCHEMA.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
            ORDER BY INDEX_NAME, SEQ_IN_INDEX
            """,
            (table,),
        )
        try:
            index_map = {}
            for index_name, column_name in cur.fetchall():
                index_map.setdefault(index_name, []).append(column_name)
            indexes = [{"name": name, "columns": cols} for name, cols in index_map.items()]
        finally:
            cur.close()
    except Exception:
        indexes = []

    return {"table": table, "row_count_estimate": row_count, "indexes": indexes}


def run_diagnostics_pipeline(connection, query, engine: str = "mysql"):
    """
    Collect EXPLAIN ANALYZE + basic session/table diagnostics for `query`.

    Called by the new backend endpoint after SELECT-only validation and after
    opening a short-lived DB connection. `engine` selects the diagnostic
    dialect ("mysql" | "postgres"); the returned shape is identical for both.
    """
    explain = get_explain_analyze(connection, query, engine)
    warnings = get_warnings(connection, engine)
    status = get_session_status(connection, engine)

    return {"explain": explain, "warnings": warnings, "status": status}

