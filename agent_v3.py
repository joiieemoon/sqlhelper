# pip install mysql-connector-python sqlglot requests

import json
import sys
import os
import re
import struct
import requests
import mysql.connector
from decimal import Decimal
from datetime import datetime, timezone
import traceback
import logging
from logging.handlers import TimedRotatingFileHandler
import socket
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3
import MySQLdb

try:
    import sqlglot
    from sqlglot import exp
    from sqlglot.errors import ParseError
except ImportError:
    # write_message not defined yet at this point — fail loudly to stderr instead
    print("Missing dependency: pip install sqlglot", file=sys.stderr)
    sys.exit(1)

MAX_QUERY_LENGTH = 8000
AGENT_VERSION = 1

_VERSIONED_COMMENT_RE = re.compile(r'/\*!\d*')

def validate_query_local(query: str):
    """SELECT-only guard — MUST run before any cursor.execute() on live DB."""
    if not query or not query.strip():
        return "Error: Empty query."
    if len(query) > MAX_QUERY_LENGTH:
        return f"Error: Query exceeds max length of {MAX_QUERY_LENGTH} characters."

    # Block MySQL versioned/executable comments before anything else —
    # these bypass AST-based validation because sqlglot treats them as
    # plain comments while MySQL itself executes their contents.
    if _VERSIONED_COMMENT_RE.search(query):
        return "Error: Query contains disallowed comment syntax."

    try:
        statements = sqlglot.parse(query, read="mysql")
    except ParseError:
        return "Error: Only SQL SELECT queries are allowed."

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return "Error: Only a single SELECT statement is allowed."

    parsed = statements[0]
    # exp.Select covers plain SELECTs (incl. CTEs). exp.Union/Except/Intersect
    # cover compound SELECTs (UNION etc.) — previously rejected as a bug.
    allowed_top_level = (exp.Select, exp.Union, exp.Except, exp.Intersect)
    if not isinstance(parsed, allowed_top_level):
        return "Error: Only SELECT queries are allowed."

    disallowed = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter,
                  exp.Create, exp.Command, exp.Merge)
    for node in parsed.walk():
        if isinstance(node, disallowed):
            return "Error: Disallowed statement type detected inside query."

    return "FINE"


class KeepAliveAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        kwargs["socket_options"] = [
            (socket.SOL_SOCKET,  socket.SO_KEEPALIVE, 1),
            (socket.IPPROTO_TCP, socket.TCP_KEEPIDLE,  20),  # probe after 20s idle
            (socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10),  # probe every 10s
            (socket.IPPROTO_TCP, socket.TCP_KEEPCNT,    5),  # 5 probes then give up
        ]
        super().init_poolmanager(*args, **kwargs)


# Try to log to the workspace log folder if it exists, otherwise use APPDATA or local log folder
log_dir = r"c:\Users\dhruv_moontechnolabs\Desktop\Query Optimiser\log"
if not os.path.exists(log_dir):
    possible_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(possible_root, "log")

try:
    os.makedirs(log_dir, exist_ok=True)
except Exception:
    pass

log_file = os.path.join(log_dir, "agent.log")

log = logging.getLogger("agent")
log.setLevel(logging.INFO)

if not log.handlers:
    try:
        file_handler = TimedRotatingFileHandler(
            log_file,
            when="D",
            interval=1,
            
            backupCount=7,
            encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        log.addHandler(file_handler)
    except Exception:
        pass

BACKEND_URL = "https://beta26.moonbeta.com/sqloptimizationtool"

ROW_THRESHOLD_COVERING  = 10_000  # min rows for a covering opportunity to be worth reporting
MIN_INDEX_SELECTIVITY   = 0.001   # indexes below this selectivity are stripped from schema
MIN_NODE_FIELDS         = 4       # signal nodes with fewer meaningful fields are stripped

# The only optimizer_switch flags that affect what the LLM can recommend
# block_nested_loop removed — deprecated in MySQL 8.0.20, replaced by hash_join
RELEVANT_OPTIMIZER_FLAGS = {
    "hash_join",
    "index_merge",
    "batched_key_access",
    "mrr",
}

# Handler counters that directly measure row access cost
# Select_scan added — counts full table scans at the statement level
HANDLER_COUNTERS = {
    "Handler_read_rnd_next",   # full table scan row reads
    "Handler_read_key",        # index lookups
    "Handler_read_next",       # index range reads
    "Handler_tmp_write",       # temp table writes
    "Select_scan",             # number of joins that did a full table scan
}


# ── Native Messaging I/O ─────────────────────────────────────────────

def read_message() -> dict:
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length or len(raw_length) < 4:
        return {}
    length = struct.unpack("<I", raw_length)[0]
    raw_message = sys.stdin.buffer.read(length)
    return json.loads(raw_message.decode("utf-8"))


def write_message(message: dict):
    encoded = json.dumps(message, default=str).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


# ── JSON Safety ──────────────────────────────────────────────────────

def make_json_safe(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [make_json_safe(v) for v in obj]
    return obj


# ── EXPLAIN ANALYZE Compressor ───────────────────────────────────────

# Fields that count as "meaningful" for the near-empty node filter
_NODE_MEANINGFUL_FIELDS = {
    "access_type", "table", "index_used", "filter_predicate",
    "total_rows_touched", "filesort", "temp_table",
    "actual_rows", "loops", "estimated_cost",
}

def compress_explain_analyze(rows):
    raw_lines = []
    for row in rows:
        text = row[0] if isinstance(row, (list, tuple)) else row
        for line in text.split("\n"):
            line = line.strip().lstrip("-> ").strip()
            if line:
                raw_lines.append(line)

    ACCESS_TYPES = [
        "Table scan", "Index range scan", "Index scan",
        "Single-row index lookup", "Covering index lookup",
        "Index lookup", "Filter", "Nested loop inner join",
        "Nested loop", "Hash join", "Sort", "Group aggregate",
        "Stream results", "Limit",
    ]

    nodes      = []
    join_order = []   # collect table names in encounter order → join sequence

    for line in raw_lines:
        node = {}

        for access in ACCESS_TYPES:
            if access.lower() in line.lower():
                node["access_type"] = access
                break

        tbl = re.search(r'\bon\s+(\w+)\b', line, re.IGNORECASE)
        if tbl:
            node["table"] = tbl.group(1)
            if tbl.group(1) not in join_order:
                join_order.append(tbl.group(1))

        idx = re.search(r'\busing\s+(\w+)', line, re.IGNORECASE)
        if idx:
            node["index_used"] = idx.group(1)

        # key_len — reported as bytes used of the index key
        key_len_m = re.search(r'\bkey_len=(\d+)', line, re.IGNORECASE)
        if key_len_m:
            node["key_len"] = int(key_len_m.group(1))

        # possible_keys — comma-separated list before the optimizer chose one
        pk_m = re.search(r'possible_keys=\[([^\]]*)\]', line, re.IGNORECASE)
        if pk_m:
            raw_keys = pk_m.group(1)
            node["possible_keys"] = [k.strip().strip('"\'') for k in raw_keys.split(",") if k.strip()]

        # ref — how the index columns are matched (const, field name, func, etc.)
        ref_m = re.search(r'\bref=([^\s,)]+)', line, re.IGNORECASE)
        if ref_m:
            node["ref"] = ref_m.group(1)

        cond = re.search(r'with index condition:\s*(.+?)(?:\s*\(|$)', line)
        if cond:
            node["index_condition"] = cond.group(1).strip()

        filter_match = re.search(r'^Filter:\s*(.+?)(?:\s*\(actual|$)', line)
        if filter_match:
            node["filter_predicate"] = filter_match.group(1).strip()

        # filtered — % of rows passing the WHERE after index scan (per table)
        filtered_m = re.search(r'\bfiltered=([0-9]+(?:\.[0-9]+)?)', line, re.IGNORECASE)
        if filtered_m:
            node["filtered"] = float(filtered_m.group(1))

        # actual_time_end_ms only — start is not useful signal for the LLM
        if "actual time=" in line:
            try:
                part       = line.split("actual time=")[1]
                time_range = part.split()[0]
                _start, end_time = time_range.split("..")
                node["actual_time_end_ms"] = float(end_time)
            except Exception:
                pass

        rows_m = re.search(r'\brows=(\d+)', line)
        if rows_m:
            node["actual_rows"] = int(rows_m.group(1))

        loops_m = re.search(r'\bloops=(\d+)', line)
        if loops_m:
            node["loops"] = int(loops_m.group(1))

        cost_m = re.search(r'cost=([0-9]+(?:\.[0-9]+)?)', line)
        if cost_m:
            node["estimated_cost"] = float(cost_m.group(1))

        est_m = re.search(r'\brows=([\d.]+)\)', line)
        if est_m:
            node["estimated_rows"] = float(est_m.group(1))

        if "actual_rows" in node and "loops" in node:
            node["total_rows_touched"] = node["actual_rows"] * node["loops"]

        # estimation_error_ratio — how far off the optimizer's row estimate was
        if "actual_rows" in node and "estimated_rows" in node and node["estimated_rows"] > 0:
            node["estimation_error_ratio"] = round(
                node["actual_rows"] / node["estimated_rows"], 3
            )

        if "filesort"  in line.lower():
            node["filesort"]   = True
        if "temporary" in line.lower():
            node["temp_table"] = True

        # sort_mode — e.g. "rowid sort", "additional fields sort"
        sort_m = re.search(r'Sort:\s*([^(]+?)(?:\s*\(|$)', line, re.IGNORECASE)
        if sort_m:
            node["sort_mode"] = sort_m.group(1).strip()

        # materialized_subquery — node is a materialisation of a subquery
        if re.search(r'materiali[sz]e', line, re.IGNORECASE):
            node["materialized_subquery"] = True

        if node:
            nodes.append(node)

    all_times  = [n["actual_time_end_ms"] for n in nodes if "actual_time_end_ms" in n]
    total_time = max(all_times) if all_times else None

    WRAPPER_TYPES = {"Nested loop inner join", "Nested loop", "Stream results", "Limit"}

    def is_signal_node(n):
        if n.get("access_type") in WRAPPER_TYPES:
            return False
        meaningful_count = sum(1 for f in _NODE_MEANINGFUL_FIELDS if f in n)
        if meaningful_count < MIN_NODE_FIELDS:
            return False
        if n.get("total_rows_touched", 0) > 50_000:
            return True
        if n.get("access_type") == "Filter" and n.get("loops", 0) > 10_000:
            return True
        if n.get("access_type") == "Sort" and n.get("actual_rows", 0) > 10_000:
            return True
        if n.get("access_type") == "Table scan":
            return True
        if n.get("estimated_cost", 0) > 10_000 and not n.get("index_used"):
            return True
        return False

    result = {
        "total_execution_time_ms": total_time,
        "has_full_table_scan":     any(n.get("access_type") == "Table scan" for n in nodes),
        "has_filesort":            any(n.get("filesort")    for n in nodes),
        "has_temp_table":          any(n.get("temp_table")  for n in nodes),
        "signal_nodes":            [n for n in nodes if is_signal_node(n)],
    }

    if join_order:
        result["join_order"] = join_order

    return result


# ── Covering Index Pre-computation ───────────────────────────────────

def compute_covering_index_opportunities(tables_schema, explain_analyze):
    """
    Returns at most ONE best covering opportunity per table, filtered to:
    - Tables with row_count >= ROW_THRESHOLD_COVERING
    - Index leading column must be a column actually used in the query
    - Optimizer is not already performing a covering scan on this table
    - 'note' field stripped
    Best = index whose cols most tightly wrap the query cols needed (fewest extra cols)
    SELECT-list columns are included in query_cols_needed via column_roles "select" role.
    """
    opportunities = []

    for table, schema in tables_schema.items():
        row_count = schema.get("row_count") or 0
        if row_count < ROW_THRESHOLD_COVERING:
            continue

        used_in_query = {col for col in schema.get("column_roles", {})}
        if not used_in_query:
            continue

        already_covered = any(
            n.get("table") == table and
            n.get("access_type") == "Covering index lookup"
            for n in explain_analyze.get("signal_nodes", [])
        )
        if already_covered:
            continue

        best_entry  = None
        best_extras = None

        for idx in schema.get("indexes", []):
            idx_cols = idx.get("cols", [])
            if not idx_cols:
                continue
            if idx_cols[0] not in used_in_query:
                continue
            if not used_in_query.issubset(set(idx_cols)):
                continue
            extras = len(idx_cols) - len(used_in_query)
            if best_extras is None or extras < best_extras:
                best_extras = extras
                best_entry  = {
                    "table":             table,
                    "index":             idx["name"],
                    "index_cols":        idx_cols,
                    "query_cols_needed": list(used_in_query),
                }

        if best_entry:
            opportunities.append(best_entry)

    return opportunities


# ── Implicit Cast Detection ───────────────────────────────────────────

_NUMERIC_TYPES  = {"int","tinyint","smallint","mediumint","bigint",
                   "float","double","decimal","numeric","bit"}
_STRING_TYPES   = {"varchar","char","text","tinytext","mediumtext",
                   "longtext","enum","set"}
_DATETIME_TYPES = {"date","datetime","timestamp","time","year"}
_JSON_TYPES     = {"json"}


def _type_family(mysql_type: str) -> str:
    base = mysql_type.lower().split("(")[0].strip()
    if base in _NUMERIC_TYPES:  return "numeric"
    if base in _STRING_TYPES:   return "string"
    if base in _DATETIME_TYPES: return "datetime"
    if base in _JSON_TYPES:     return "json"
    return "other"


def _literal_family(node):
    if isinstance(node, exp.Literal):
        return "string" if node.is_string else "numeric"
    if isinstance(node, (exp.CurrentTimestamp, exp.CurrentDate)):
        return "datetime"
    return None


def detect_implicit_casts(query: str, tables_schema: dict, meta: dict) -> list:
    """
    Returns implicit cast issues with 'literal_value' and 'impact' stripped.
    The LLM reasons about impact itself from row_count and column_roles.
    """
    parsed    = sqlglot.parse_one(query, read="mysql")
    alias_map = meta.get("alias_map", {})
    issues    = []

    col_type_map = {}
    for table, schema in tables_schema.items():
        for col in schema.get("columns", []):
            col_type_map[f"{table}.{col['name']}"] = _type_family(col["type"])

    def resolve_col_type(col_node):
        tbl = alias_map.get(col_node.table, col_node.table)
        return col_type_map.get(f"{tbl}.{col_node.name}")

    COMPARISON_NODES = (
        exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
        exp.In, exp.Between,
    )

    def check_node(node):
        if isinstance(node, (exp.JSONExtract, exp.JSONExtractScalar)):
            col = node.find(exp.Column)
            if col:
                tbl = alias_map.get(col.table, col.table)
                issues.append({
                    "type":       "json_column_filter",
                    "table":      tbl,
                    "column":     col.name,
                    "expression": node.sql(),
                    "reason": (
                        f"{tbl}.{col.name} uses JSON_EXTRACT. "
                        "Standard B-tree indexes cannot be used on JSON paths."
                    ),
                })

        if isinstance(node, COMPARISON_NODES):
            col_node     = None
            literal_node = None
            for child in list(node.args.values()):
                if isinstance(child, exp.Column):
                    col_node = child
                elif isinstance(child, exp.Literal):
                    literal_node = child

            if col_node and literal_node:
                col_fam = resolve_col_type(col_node)
                lit_fam = _literal_family(literal_node)
                if col_fam and lit_fam and col_fam != lit_fam:
                    tbl = alias_map.get(col_node.table, col_node.table)
                    issues.append({
                        "type":         "type_mismatch",
                        "table":        tbl,
                        "column":       col_node.name,
                        "column_type":  col_fam,
                        "literal_type": lit_fam,
                        "expression":   node.sql(),
                        "reason": (
                            f"{tbl}.{col_node.name} is {col_fam} but compared "
                            f"to a {lit_fam} literal. MySQL will cast implicitly, "
                            "disabling index use on this column."
                        ),
                    })

    where = parsed.find(exp.Where)
    if where:
        for node in where.walk():
            check_node(node)

    for join in parsed.find_all(exp.Join):
        on = join.args.get("on")
        if on:
            for node in on.walk():
                check_node(node)

    return issues


# ── Function-Wrapped Column Detection ────────────────────────────────

def detect_function_wrapped_columns(query: str, tables_schema: dict, meta: dict) -> list:
    """
    Finds columns used inside function calls in WHERE / JOIN ON clauses.
    Functions applied to indexed columns prevent index use entirely.
    e.g. WHERE YEAR(created_at) = 2024  →  index on created_at is bypassed.
    """
    parsed    = sqlglot.parse_one(query, read="mysql")
    alias_map = meta.get("alias_map", {})
    issues    = []

    # Build set of indexed columns per table for relevance filtering
    indexed_cols = {}
    for table, schema in tables_schema.items():
        indexed_cols[table] = set()
        for idx in schema.get("indexes", []):
            for col in idx.get("cols", []):
                indexed_cols[table].add(col)

    def _check_clause(clause_node):
        if clause_node is None:
            return
        # Walk all Anonymous/named function calls
        for func_node in clause_node.find_all(exp.Anonymous, exp.Func):
            for col_node in func_node.find_all(exp.Column):
                tbl = alias_map.get(col_node.table, col_node.table) or "unknown"
                col = col_node.name
                # Only report if the column actually has an index on it
                if tbl in indexed_cols and col in indexed_cols[tbl]:
                    issues.append({
                        "type":       "function_wrapped_column",
                        "table":      tbl,
                        "column":     col,
                        "expression": func_node.sql(),
                        "reason": (
                            f"{tbl}.{col} is wrapped in a function call. "
                            "MySQL cannot use a B-tree index on this column; "
                            "consider a generated column + index or rewriting the predicate."
                        ),
                    })

    _check_clause(parsed.find(exp.Where))
    for join in parsed.find_all(exp.Join):
        _check_clause(join.args.get("on"))

    # Deduplicate by (table, column, expression)
    seen   = set()
    unique = []
    for issue in issues:
        key = (issue["table"], issue["column"], issue["expression"])
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique


# ── Subquery Pattern Detection ────────────────────────────────────────

def detect_subquery_patterns(query: str, tables_schema: dict, meta: dict) -> list:
    """
    Detects two high-cost subquery patterns:
    1. Correlated subqueries — reference an outer-query column inside the subquery.
       MySQL re-executes them once per outer row.
    2. NOT IN with a nullable RHS column — NULL in the subquery result makes the
       entire NOT IN return UNKNOWN (no rows), a silent correctness trap.
    """
    parsed    = sqlglot.parse_one(query, read="mysql")
    alias_map = meta.get("alias_map", {})
    issues    = []

    # Build nullable column set across all schema tables
    nullable_cols = set()
    for table, schema in tables_schema.items():
        for col in schema.get("columns", []):
            if col.get("nullable"):
                nullable_cols.add(f"{table}.{col['name']}")

    # Collect all outer-scope column references (above any subquery)
    outer_cols = set()
    for col in parsed.find_all(exp.Column):
        tbl = alias_map.get(col.table, col.table) or ""
        outer_cols.add(f"{tbl}.{col.name}".lower())

    for subquery in parsed.find_all(exp.Subquery):
        sq_sql = subquery.sql()

        # ── Correlated subquery check ────────────────────────────────
        for col in subquery.find_all(exp.Column):
            tbl = alias_map.get(col.table, col.table) or ""
            fqn = f"{tbl}.{col.name}".lower()
            # If this column resolves to an outer table it's a correlation
            if tbl and tbl in {alias_map.get(t, t) for t in meta.get("tables", set())}:
                # Check whether the same column also appears outside the subquery
                # (simple heuristic: outer_cols built from top-level parse)
                if fqn in outer_cols:
                    issues.append({
                        "type":       "correlated_subquery",
                        "column":     col.name,
                        "table":      tbl,
                        "expression": sq_sql[:200],
                        "reason": (
                            f"Subquery references outer column {tbl}.{col.name}. "
                            "MySQL re-executes this subquery for every row of the outer query. "
                            "Rewrite as a JOIN or use a derived table."
                        ),
                    })
                    break  # one issue per subquery is enough

        # ── NOT IN with nullable column check ────────────────────────
        parent = subquery.parent
        if isinstance(parent, exp.In) and parent.args.get("is_not"):
            # Find the column being selected inside the subquery
            inner_select = subquery.find(exp.Select)
            if inner_select:
                for sel_col in inner_select.find_all(exp.Column):
                    tbl      = alias_map.get(sel_col.table, sel_col.table) or ""
                    fqn_full = f"{tbl}.{sel_col.name}"
                    if fqn_full in nullable_cols:
                        issues.append({
                            "type":       "not_in_nullable_column",
                            "column":     sel_col.name,
                            "table":      tbl,
                            "expression": parent.sql()[:200],
                            "reason": (
                                f"NOT IN subquery selects {fqn_full} which is nullable. "
                                "If any row returns NULL, the entire NOT IN evaluates to UNKNOWN "
                                "and no rows are returned. Use NOT EXISTS instead."
                            ),
                        })
                        break

    # Deduplicate
    seen   = set()
    unique = []
    for issue in issues:
        key = (issue["type"], issue.get("table"), issue.get("column"))
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique


# ── OR on Indexed Column Detection ───────────────────────────────────

def detect_or_on_indexed_columns(query: str, tables_schema: dict, meta: dict) -> list:
    """
    Finds OR conditions in WHERE clauses where both sides reference the same
    indexed column. MySQL often cannot use the index efficiently for OR predicates
    unless index_merge is active — worth flagging so the LLM can recommend
    UNION rewrite or verify index_merge is on.
    """
    parsed    = sqlglot.parse_one(query, read="mysql")
    alias_map = meta.get("alias_map", {})
    issues    = []

    indexed_cols = {}
    for table, schema in tables_schema.items():
        indexed_cols[table] = set()
        for idx in schema.get("indexes", []):
            for col in idx.get("cols", []):
                indexed_cols[table].add(col)

    where = parsed.find(exp.Where)
    if not where:
        return issues

    for or_node in where.find_all(exp.Or):
        cols_seen = {}
        for col in or_node.find_all(exp.Column):
            tbl = alias_map.get(col.table, col.table) or "unknown"
            key = (tbl, col.name)
            cols_seen[key] = cols_seen.get(key, 0) + 1

        for (tbl, col_name), count in cols_seen.items():
            if count >= 2 and tbl in indexed_cols and col_name in indexed_cols[tbl]:
                issues.append({
                    "type":       "or_on_indexed_column",
                    "table":      tbl,
                    "column":     col_name,
                    "expression": or_node.sql()[:200],
                    "reason": (
                        f"{tbl}.{col_name} appears in an OR predicate. "
                        "MySQL may not use the index unless index_merge is active. "
                        "Consider rewriting as UNION ALL of two separate range scans."
                    ),
                })

    seen   = set()
    unique = []
    for issue in issues:
        key = (issue["table"], issue["column"])
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique


# ── Optimizer Trace ───────────────────────────────────────────────────

def get_optimizer_trace(cursor, query: str) -> dict:
    """
    Enables the optimizer trace for a single query execution and extracts:
    - per-table considered access paths with their costs
    - winning plan cost
    - rejected index names with the reason/cost that caused rejection
    Disables the trace immediately after to avoid overhead on subsequent queries.
    """
    result = {}
    try:
        cursor.execute("SET optimizer_trace='enabled=on,one_line=off'")
        cursor.execute("SET optimizer_trace_max_mem_size=1048576")

        # Run the query so the trace is populated — discard results
        cursor.execute(query)
        cursor.fetchall()

        cursor.execute("""
            SELECT TRACE FROM information_schema.optimizer_trace LIMIT 1
        """)
        row = cursor.fetchone()

        cursor.execute("SET optimizer_trace='enabled=off'")

        if not row or not row[0]:
            return result

        trace = json.loads(row[0])

        # Navigate to the join_optimization → table sections
        steps = (
            trace
            .get("steps", [{}])[0]
            .get("join_optimization", {})
            .get("steps", [])
        )

        access_paths = {}   # table → list of {access, cost}
        rejected     = {}   # table → list of {index, reason, cost}
        winning_cost = None

        for step in steps:
            # Considered_execution_plans holds the winning plan cost
            cep = step.get("considered_execution_plans", [])
            for plan in cep:
                cost = plan.get("best_access_path", {}).get("cost")
                if cost is not None and winning_cost is None:
                    winning_cost = cost

            # rows_estimation → table_scan vs range_analysis per table
            rows_est = step.get("rows_estimation", [])
            for entry in rows_est:
                tbl = entry.get("table", "").lstrip("`").rstrip("`")
                if not tbl:
                    continue

                paths = []
                ra    = entry.get("range_analysis", {})

                # Table scan baseline
                ts_cost = entry.get("table_scan", {}).get("cost")
                if ts_cost is not None:
                    paths.append({"access": "table_scan", "cost": ts_cost})

                # Each analyzed index
                for idx_entry in ra.get("analyzing_range_alternatives", {}).get("range_scan_alternatives", []):
                    idx_name  = idx_entry.get("index")
                    idx_cost  = idx_entry.get("index_scan_cost") or idx_entry.get("cost")
                    chosen    = idx_entry.get("chosen", False)
                    cause     = idx_entry.get("cause", "")

                    if idx_name:
                        paths.append({
                            "access": f"range:{idx_name}",
                            "cost":   idx_cost,
                            "chosen": chosen,
                        })
                        if not chosen and idx_name:
                            rejected.setdefault(tbl, []).append({
                                "index":  idx_name,
                                "reason": cause or "not_chosen",
                                "cost":   idx_cost,
                            })

                if paths:
                    access_paths[tbl] = paths

        if access_paths:
            result["access_paths_per_table"] = access_paths
        if winning_cost is not None:
            result["winning_plan_cost"] = winning_cost
        if rejected:
            result["rejected_indexes"] = rejected

    except Exception:
        cursor.execute("SET optimizer_trace='enabled=off'")

    return result


# ── InnoDB Table Stats ───────────────────────────────────────────────

def get_innodb_table_stats(cursor, tables, database):
    """
    Collects n_rows and derives stats_age_days from last_update.
    clustered_index_size_pages and other_indexes_size_pages removed —
    they don't affect query optimization recommendations.
    """
    stats = {}
    now   = datetime.now(timezone.utc)
    for table in tables:
        try:
            cursor.execute("""
                SELECT n_rows, last_update
                FROM mysql.innodb_table_stats
                WHERE database_name = %s
                  AND LOWER(table_name) = LOWER(%s)
            """, [database, table])
            row = cursor.fetchone()
            if row:
                n_rows, last_update = row
                age_days = None
                if last_update:
                    try:
                        if last_update.tzinfo is None:
                            last_update = last_update.replace(tzinfo=timezone.utc)
                        age_days = (now - last_update).days
                    except Exception:
                        pass
                stats[table] = {
                    "n_rows":          n_rows,
                    "stats_age_days":  age_days,
                }
        except Exception:
            pass
    return stats


# ── Optimizer Warnings (SHOW WARNINGS) ──────────────────────────────

def get_optimizer_warnings(cursor) -> dict:
    """
    Must be called on the same cursor immediately after EXPLAIN ANALYZE
    fetchall() — MySQL clears the warning buffer on the next statement.
    """
    result = {}
    try:
        cursor.execute("SHOW WARNINGS")
        rows = cursor.fetchall()
    except Exception:
        return result

    warnings    = []
    rewritten_q = None

    for level, code, message in rows:
        code = int(code) if code is not None else None

        if code == 1003:
            cleaned = re.sub(r'^/\*.*?\*/\s*', '', message, flags=re.DOTALL).strip()
            rewritten_q = cleaned
            continue

        if level in ("Warning", "Note"):
            warnings.append({
                "level":   level,
                "code":    code,
                "message": message,
            })

    if warnings:
        result["optimizer_warnings"] = warnings
    if rewritten_q:
        result["optimizer_rewritten_query"] = rewritten_q

    return result


# ── Optimizer Switch (filtered to relevant flags only) ───────────────

def get_optimizer_switch(cursor) -> dict:
    """
    Returns only the 4 optimizer_switch flags that affect what the LLM
    can meaningfully recommend. block_nested_loop removed (deprecated MySQL 8.0.20+).
    """
    try:
        cursor.execute("SELECT @@optimizer_switch")
        row = cursor.fetchone()
        if not row:
            return {}
        raw    = row[0]
        result = {}
        for part in raw.split(","):
            part = part.strip()
            if "=" not in part:
                continue
            flag, value = part.split("=", 1)
            flag = flag.strip()
            if flag in RELEVANT_OPTIMIZER_FLAGS:
                result[flag] = value.strip() == "on"
        return result
    except Exception:
        return {}


# ── Handler Session Status (row access ground truth) ─────────────────

def read_handler_counters(cursor) -> dict:
    """
    Read Handler_* and Select_scan session status counters.
    Call once after FLUSH STATUS and once after query execution to diff.
    """
    try:
        placeholders = ", ".join(f"'{c}'" for c in HANDLER_COUNTERS)
        cursor.execute(f"""
            SELECT VARIABLE_NAME, VARIABLE_VALUE
            FROM performance_schema.session_status
            WHERE VARIABLE_NAME IN ({placeholders})
        """)
        return {row[0]: int(row[1]) for row in cursor.fetchall()}
    except Exception:
        return {}


def diff_handler_counters(before: dict, after: dict) -> dict:
    """
    Returns delta for each counter (positive deltas only) plus three
    derived boolean flags that tell the LLM what actually happened at runtime.
    """
    result = {}
    for key in HANDLER_COUNTERS:
        delta = after.get(key, 0) - before.get(key, 0)
        if delta > 0:
            result[key] = delta

    # Derived booleans — computed from the deltas, not raw counters
    tmp_writes = result.get("Handler_tmp_write", 0)
    rnd_next   = result.get("Handler_read_rnd_next", 0)

    # spill_to_disk: temp table writes indicate sort/join spilled beyond memory
    if tmp_writes > 0:
        result["spill_to_disk"] = True

    # sort_buffer_overflow: large rnd_next alongside tmp writes strongly implies
    # the sort buffer was exceeded and MySQL fell back to disk-based sort
    if tmp_writes > 0 and rnd_next > 10_000:
        result["sort_buffer_overflow"] = True

    # join_without_index: high rnd_next with no index reads = full scan join
    index_reads = result.get("Handler_read_key", 0) + result.get("Handler_read_next", 0)
    if rnd_next > 1_000 and index_reads == 0:
        result["join_without_index"] = True

    return result


# ── Column Statistics (histogram presence) ───────────────────────────

def get_column_statistics(cursor, tables_schema: dict, meta: dict) -> dict:
    """
    Checks INFORMATION_SCHEMA.COLUMN_STATISTICS for histograms on WHERE-clause
    columns. Histograms improve cardinality estimation; their absence on
    high-filter columns is worth flagging.
    Returns {table: {column: {has_histogram, bucket_count}}}
    """
    result = {}
    where_cols = meta.get("where", {})

    for table, cols in where_cols.items():
        if not cols:
            continue
        for col in cols:
            try:
                cursor.execute("""
                    SELECT HISTOGRAM->>'$.buckets' IS NOT NULL AS has_histogram,
                           JSON_LENGTH(HISTOGRAM->'$.buckets')  AS bucket_count
                    FROM information_schema.COLUMN_STATISTICS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME   = %s
                      AND COLUMN_NAME  = %s
                """, [table, col])
                row = cursor.fetchone()
                if row:
                    result.setdefault(table, {})[col] = {
                        "has_histogram": bool(row[0]),
                        "bucket_count":  row[1],
                    }
                else:
                    result.setdefault(table, {})[col] = {
                        "has_histogram": False,
                        "bucket_count":  None,
                    }
            except Exception:
                pass

    return result


# ── Server Variables ─────────────────────────────────────────────────

def get_server_variables(cursor) -> dict:
    """
    Fetches the 8 buffer/config variables that directly affect whether
    sorts, joins, and temp tables stay in memory or spill to disk.
    """
    VARS = [
        "sort_buffer_size",
        "join_buffer_size",
        "tmp_table_size",
        "max_heap_table_size",
        "innodb_buffer_pool_size",
        "eq_range_index_dive_limit",
        "innodb_stats_persistent_sample_pages",
        "read_rnd_buffer_size",
    ]
    try:
        placeholders = ", ".join(f"@@{v}" for v in VARS)
        cursor.execute(f"SELECT {placeholders}")
        row = cursor.fetchone()
        if not row:
            return {}
        return {var: row[i] for i, var in enumerate(VARS)}
    except Exception:
        return {}


# ── sys.schema_index_statistics ──────────────────────────────────────

def get_sys_index_stats(cursor, tables: set) -> dict:
    """
    Returns rows_selected per index per table from sys.schema_index_statistics.
    Indexes with zero rows_selected are omitted — they're unused and add no signal.
    """
    result = {}
    if not tables:
        return result
    try:
        placeholders = ", ".join(["%s"] * len(tables))
        cursor.execute(f"""
            SELECT table_name, index_name, rows_selected
            FROM sys.schema_index_statistics
            WHERE table_schema = DATABASE()
              AND table_name IN ({placeholders})
              AND rows_selected > 0
        """, list(tables))
        for table_name, index_name, rows_sel in cursor.fetchall():
            result.setdefault(table_name, {})[index_name] = rows_sel
    except Exception:
        pass
    return result


# ── Schema Extraction ────────────────────────────────────────────────

def extract_query_metadata(query):
    parsed    = sqlglot.parse_one(query, read="mysql")
    tables    = set()
    alias_map = {}
    where_cols          = {}
    join_cols           = {}
    order_cols          = {}
    group_cols_by_table = {}
    select_cols         = {}   # NEW: columns in the SELECT list
    joins               = []

    for table in parsed.find_all(exp.Table):
        alias_map[table.alias_or_name] = table.name
        tables.add(table.name)

    # SELECT-list columns — needed for covering index opportunities
    select_node = parsed.find(exp.Select)
    if select_node:
        for col in select_node.find_all(exp.Column):
            t = alias_map.get(col.table, col.table)
            select_cols.setdefault(t or "unknown", set()).add(col.name)

    where = parsed.find(exp.Where)
    if where:
        for col in where.find_all(exp.Column):
            t = alias_map.get(col.table, col.table)
            where_cols.setdefault(t or "unknown", set()).add(col.name)

    for join in parsed.find_all(exp.Join):
        on = join.args.get("on")
        if on:
            cols = list(on.find_all(exp.Column))
            if len(cols) >= 2:
                l, r = cols[0], cols[1]
                joins.append({
                    "left":  f"{alias_map.get(l.table, l.table)}.{l.name}",
                    "right": f"{alias_map.get(r.table, r.table)}.{r.name}",
                })
            for col in cols:
                t = alias_map.get(col.table, col.table)
                join_cols.setdefault(t or "unknown", set()).add(col.name)

    order = parsed.find(exp.Order)
    if order:
        for col in order.find_all(exp.Column):
            t = alias_map.get(col.table, col.table)
            order_cols.setdefault(t or "unknown", set()).add(col.name)

    group = parsed.find(exp.Group)
    if group:
        for col in group.find_all(exp.Column):
            t = alias_map.get(col.table, col.table)
            group_cols_by_table.setdefault(t or "unknown", set()).add(col.name)

    return {
        "tables":    tables,
        "alias_map": alias_map,
        "where":     where_cols,
        "join":      join_cols,
        "order":     order_cols,
        "group":     group_cols_by_table,
        "select":    select_cols,   # NEW
        "joins":     joins,
    }


def _build_query_cols(meta, table):
    """All columns from this table that appear anywhere in the query."""
    return (
        meta["where"].get(table, set())
        | meta["join"].get(table, set())
        | meta["order"].get(table, set())
        | meta["group"].get(table, set())
        | meta.get("select", {}).get(table, set())   # NEW: include SELECT cols
    )


def _filter_indexes(indexes, query_cols, row_count):
    """
    Keep an index if:
    - It is the PRIMARY key, OR
    - At least one of its columns appears in the query
      AND selectivity >= MIN_INDEX_SELECTIVITY
    """
    kept = []
    for idx in indexes:
        if idx.get("name") == "PRIMARY":
            kept.append(idx)
            continue
        if not any(c in query_cols for c in idx.get("cols", [])):
            continue
        selectivity = idx.get("selectivity")
        if selectivity is not None and selectivity < MIN_INDEX_SELECTIVITY:
            continue
        kept.append(idx)
    return kept


def get_table_schema(cursor, table, meta, row_count):
    relevant_cols = _build_query_cols(meta, table)

    columns = []
    if relevant_cols:
        placeholders = ",".join(["%s"] * len(relevant_cols))
        cursor.execute(f"""
            SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME   = %s
              AND COLUMN_NAME IN ({placeholders})
            ORDER BY ORDINAL_POSITION
        """, [table, *relevant_cols])
        for col_name, col_type, nullable in cursor.fetchall():
            columns.append({
                "name":     col_name,
                "type":     col_type,
                "nullable": nullable == "YES",
            })

    # avg_row_length + fragmentation_pct from SHOW TABLE STATUS
    avg_row_length   = None
    fragmentation_pct = None
    try:
        cursor.execute(f"SHOW TABLE STATUS LIKE %s", [table])
        ts_row = cursor.fetchone()
        if ts_row:
            # SHOW TABLE STATUS columns (0-indexed):
            # 0:Name 1:Engine 2:Version 3:Row_format 4:Rows 5:Avg_row_length
            # 6:Data_length 7:Max_data_length 8:Index_length 9:Data_free ...
            avg_row_length = ts_row[5]
            data_length  = ts_row[6] or 0
            index_length = ts_row[8] or 0
            data_free    = ts_row[9] or 0
            total        = data_length + index_length + data_free
            if total > 0:
                fragmentation_pct = round((data_free / total) * 100, 2)
    except Exception:
        pass

    # Index metadata — now includes SUB_PART, NULLABLE, IS_VISIBLE
    cursor.execute("""
        SELECT INDEX_NAME, COLUMN_NAME, NON_UNIQUE, CARDINALITY,
               INDEX_TYPE, SEQ_IN_INDEX, SUB_PART, NULLABLE, IS_VISIBLE
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
        ORDER BY INDEX_NAME, SEQ_IN_INDEX
    """, [table])

    index_map = {}
    for idx, col, non_unique, card, idx_type, _, sub_part, nullable_idx, is_visible in cursor.fetchall():
        if idx not in index_map:
            index_map[idx] = {
                "name":        idx,
                "cols":        [],
                "unique":      not bool(non_unique),
                "cardinality": card,
                "type":        idx_type.lower(),
                "sub_parts":   [],   # prefix lengths per column (None = full column)
                "nullable":    [],   # whether each column allows NULLs in the index
                "is_visible":  True, # will be overwritten per-column; last wins (same for all cols)
            }
        index_map[idx]["cols"].append(col)
        index_map[idx]["sub_parts"].append(sub_part)       # int or None
        index_map[idx]["nullable"].append(nullable_idx == "YES")
        index_map[idx]["is_visible"] = (is_visible == "YES")

    raw_indexes = []
    for idx_data in index_map.values():
        card = idx_data.get("cardinality")
        if card and row_count and row_count > 0:
            idx_data["selectivity"] = round(card / row_count, 4)
        raw_indexes.append(idx_data)

    indexes = _filter_indexes(raw_indexes, relevant_cols, row_count)

    # column_roles — now includes "select" clause
    column_roles = {}
    for col in [c["name"] for c in columns]:
        roles = []
        if col in meta["where"].get(table, set()):              roles.append("filter")
        if col in meta["join"].get(table, set()):               roles.append("join")
        if col in meta["order"].get(table, set()):              roles.append("order")
        if col in meta["group"].get(table, set()):              roles.append("group")
        if col in meta.get("select", {}).get(table, set()):     roles.append("select")
        if roles:
            column_roles[col] = roles

    schema = {
        "row_count":      row_count,
        "avg_row_length": avg_row_length,
        "columns":        columns,
        "column_roles":   column_roles,
        "indexes":        indexes,
    }
    if fragmentation_pct is not None:
        schema["fragmentation_pct"] = fragmentation_pct

    return schema


def extract_schema(query, connection, database):
    cursor = connection.cursor()
    meta   = extract_query_metadata(query)

    row_counts = {}
    for table in meta["tables"]:
        try:
            cursor.execute("""
                SELECT TABLE_ROWS FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
            """, [table])
            r = cursor.fetchone()
            row_counts[table] = r[0] if r else None
        except Exception:
            row_counts[table] = None

    tables_schema = {}
    for table in meta["tables"]:
        try:
            tables_schema[table] = get_table_schema(
                cursor, table, meta, row_counts.get(table)
            )
        except Exception:
            pass

    cursor.close()
    return tables_schema, meta


# ── Main ─────────────────────────────────────────────────────────────

def handle_request(body: dict) -> dict:
    host           = body.get("host")
    port           = body.get("port", 3306)
    user           = body.get("user")
    password       = body.get("password")
    database       = body.get("database")
    query          = body.get("query")
    scope          = body.get("scope", [])
    custom_request = body.get("custom_request", "")
    device_id = body.get("device_id", "")

    print(f"DEBUG device_id received: '{device_id}'", file=sys.stderr)

    # ── SELECT-only guard — runs BEFORE any DB connection is opened ──────
    # This is the critical safety fix: previously EXPLAIN ANALYZE could
    # execute non-SELECT statements (DELETE/UPDATE/etc) against the live
    # DB before backend validation ever ran. Now we block here first.
    validation = validate_query_local(query)
    if validation != "FINE":
        log.warning(f"Rejected non-SELECT query locally (pre-DB-connect): {validation}")
        return {"error": validation}

    try:
        # conn = mysql.connector.connect(
        #     host=host,
        #     port=int(port),
        #     user=user,
        #     password=password,
        #     database=database,
        #     connection_timeout=10,
        # )
        conn = MySQLdb.connect(
            host=host, port=int(port), user=user, passwd=password,
            db=database, connect_timeout=10,
        )
    except mysql.connector.errors.NotSupportedError:
        # Plugin auto-negotiation failed to load — retry forcing each known plugin
        conn = None
        last_err = None
        for plugin in ("caching_sha2_password", "mysql_native_password"):
            try:
                conn = mysql.connector.connect(
                    host=host,
                    port=int(port),
                    user=user,
                    password=password,
                    database=database,
                    connection_timeout=10,
                    auth_plugin=plugin,
                )
                break
            except Exception as e:
                last_err = e
                continue
        if conn is None:
            return {"error": f"DB connection failed: {str(last_err)}"}
    except Exception as e:
        return {"error": f"DB connection failed: {str(e)}"}

    try:
        # ── Step 1: FLUSH STATUS → read Handler baseline ──────────────
        # Single baseline read — the dead first read before FLUSH is removed
        cursor = conn.cursor()
        try:
            cursor.execute("FLUSH STATUS")
        except Exception:
            pass
        handler_before = read_handler_counters(cursor)

        # ── Step 2: EXPLAIN ANALYZE + SHOW WARNINGS ───────────────────
        cursor.execute(f"EXPLAIN ANALYZE {query}")
        explain_analyze_raw = cursor.fetchall()
        # SHOW WARNINGS must run before any other statement on this cursor
        optimizer_notes = get_optimizer_warnings(cursor)
        handler_after   = read_handler_counters(cursor)
        cursor.close()

        explain_analyze = compress_explain_analyze(explain_analyze_raw)
        handler_diff    = diff_handler_counters(handler_before, handler_after)

        # ── Step 3: Schema extraction ─────────────────────────────────
        tables_schema, meta = extract_schema(query, conn, database)

        covering_opportunities     = compute_covering_index_opportunities(
            tables_schema, explain_analyze
        )
        implicit_cast_issues       = detect_implicit_casts(query, tables_schema, meta)
        function_wrapped_issues    = detect_function_wrapped_columns(query, tables_schema, meta)
        subquery_pattern_issues    = detect_subquery_patterns(query, tables_schema, meta)
        or_indexed_col_issues      = detect_or_on_indexed_columns(query, tables_schema, meta)

        # ── Step 4: DB-side diagnostics ───────────────────────────────
        cursor = conn.cursor()
        innodb_stats     = get_innodb_table_stats(cursor, meta["tables"], database)
        optimizer_switch = get_optimizer_switch(cursor)
        column_stats     = get_column_statistics(cursor, tables_schema, meta)
        server_vars      = get_server_variables(cursor)
        sys_index_stats  = get_sys_index_stats(cursor, meta["tables"])
        cursor.close()

        # ── Step 5: Optimizer trace (separate connection to avoid state bleed)
        optimizer_trace = {}
        try:
            trace_cursor = conn.cursor()
            optimizer_trace = get_optimizer_trace(trace_cursor, query)
            trace_cursor.close()
        except Exception:
            pass

        conn.close()

        # ── Step 6: Build payload ─────────────────────────────────────
        payload = make_json_safe({
            "query": query,
            "agent_version": AGENT_VERSION,
            "explain_analyze": explain_analyze,
            "schema":          tables_schema,
            "scope":           scope,
            "custom_request":  custom_request,
        })

        # Covering + static analysis
        if covering_opportunities:
            payload["covering_opportunities"] = covering_opportunities
        if implicit_cast_issues:
            payload["implicit_cast_issues"] = implicit_cast_issues
        if function_wrapped_issues:
            payload["function_wrapped_column_issues"] = function_wrapped_issues
        if subquery_pattern_issues:
            payload["subquery_pattern_issues"] = subquery_pattern_issues
        if or_indexed_col_issues:
            payload["or_on_indexed_column_issues"] = or_indexed_col_issues

        # Runtime stats
        if handler_diff:
            payload["handler_stats"] = handler_diff
        if innodb_stats:
            payload["innodb_stats"] = innodb_stats
        if column_stats:
            payload["column_statistics"] = column_stats
        if server_vars:
            payload["server_variables"] = server_vars
        if sys_index_stats:
            payload["sys_index_stats"] = sys_index_stats

        # Optimizer metadata
        if optimizer_switch:
            payload["optimizer_switch"] = optimizer_switch
        if optimizer_trace:
            payload["optimizer_trace"] = optimizer_trace
        if "optimizer_warnings" in optimizer_notes:
            payload["optimizer_warnings"] = optimizer_notes["optimizer_warnings"]
        if "optimizer_rewritten_query" in optimizer_notes:
            payload["optimizer_rewritten_query"] = optimizer_notes["optimizer_rewritten_query"]

        # NEW — replace with this
        session = requests.Session()
        session.mount("http://",  KeepAliveAdapter())
        session.mount("https://", KeepAliveAdapter())

        res = session.post(
            f"{BACKEND_URL}/agent_optimize",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Device-ID": device_id,
            },
            timeout=(15, 260),
        )

        print("STATUS:", res.status_code, file=sys.stderr)
        print("BODY:", res.text[:5000], file=sys.stderr)
        log.info(f"BACKEND STATUS: {res.status_code}")
        log.info(f"BACKEND CONTENT-TYPE: {res.headers.get('content-type')}")
        log.info(f"BACKEND BODY: {res.text[:5000]}")

        try:
            return res.json()
        except Exception:
            return {
                "error": f"Backend returned non-json response. Status={res.status_code}",
                "body": res.text[:1000]
            }

    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return {"error": str(e)}


if __name__ == "__main__":
    try:
        message = read_message()
        if not message:
            log.warning("No input received")
            write_message({"error": "No input received"})
            sys.exit(1)

        # ── Version handshake — extension asks this before sending real work ──
        if message.get("type") == "GET_VERSION":
            write_message({"version": AGENT_VERSION})
            sys.exit(0)

        logged_message = message.copy()
        if "password" in logged_message:
            logged_message["password"] = "********"
        log.info(f"Input received: {json.dumps(logged_message, default=str)}")

        result = handle_request(message)
        log.info(f"Output returned: {json.dumps(result, default=str)}")
        write_message(result)

    except Exception as e:
        log.error(f"Error in main: {str(e)}")
        write_message({"error": str(e)})
        sys.exit(1)