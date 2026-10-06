# -*- coding: utf-8 -*-
"""
db_schema.py â€” Schema extraction for the direct-DB optimization flow.

Functions are reused from agent_v3.py (the former local Chrome agent) with
identical SQL and identical output shape, so the 3-stage LLM pipeline keeps
consuming the same schema format. Copied rather than imported because
agent_v3.py has a hard top-level `import MySQLdb` (not installed on the
server) â€” agent_v3.py itself remains untouched per the R&D task rules.

Only SELECT-only INFORMATION_SCHEMA reads are performed here.
"""

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

_MIN_INDEX_SELECTIVITY = 0.0001


def _resolve_table(col, alias_map: dict, tables: set) -> str:
    """
    Resolve a column's owning table.

    agent_v3's original maps unqualified columns (col.table == '') to an
    'unknown' bucket, which leaves the per-table schema empty for plain
    queries like `SELECT * FROM customers WHERE country = 'USA'`. Here we
    keep qualified resolution via alias_map, and resolve unqualified
    columns to THE single referenced table (mirrors MySQL semantics).
    Genuinely ambiguous multi-table cases stay unresolvable -> ''.
    """
    t = getattr(col, "table", "") or ""
    if t:
        return alias_map.get(t, t)
    if len(tables) == 1:
        return next(iter(tables))
    return ""


def _join_entry(cols, alias_map: dict) -> dict:
    """{left, right} pair for a two-column join predicate, alias-resolved."""
    left, right = cols[0], cols[1]
    return {
        "left":  f"{alias_map.get(left.table, left.table)}.{left.name}",
        "right": f"{alias_map.get(right.table, right.table)}.{right.name}",
    }


def extract_query_metadata(query: str, dialect: str = "mysql") -> dict:
    """Table/alias/column usage extraction (mirrors agent_v3.extract_query_metadata).

    `dialect` is passed to sqlglot so PostgreSQL-specific syntax (::casts,
    ILIKE, DISTINCT ON, ...) parses instead of raising ParseError.
    """
    parsed = sqlglot.parse_one(query, read=dialect)
    tables, alias_map = set(), {}
    where_cols, join_cols, order_cols = {}, {}, {}
    group_cols_by_table, select_cols = {}, {}
    joins = []

    for table in parsed.find_all(exp.Table):
        alias_map[table.alias_or_name] = table.name
        tables.add(table.name)

    select_node = parsed.find(exp.Select)
    if select_node:
        for col in select_node.find_all(exp.Column):
            t = _resolve_table(col, alias_map, tables)
            select_cols.setdefault(t or "unknown", set()).add(col.name)

    where = parsed.find(exp.Where)
    if where:
        for col in where.find_all(exp.Column):
            t = _resolve_table(col, alias_map, tables)
            where_cols.setdefault(t or "unknown", set()).add(col.name)

    # `JOIN ... ON` carries its predicate on the join node itself. CROSS JOIN
    # and comma-style `FROM a, b` do not: sqlglot emits a Join with no `on`
    # because the pairing predicate lives in WHERE (there is none at all for a
    # true cartesian product). Counting those lets us recover them below —
    # without it this metadata reports joins=[] for queries that plainly join,
    # so an issue-finder asked for scope="joins" is told no join exists.
    conditionless_joins = 0
    for join in parsed.find_all(exp.Join):
        on = join.args.get("on")
        if on:
            cols = list(on.find_all(exp.Column))
            if len(cols) >= 2:
                joins.append(_join_entry(cols, alias_map))
            for col in cols:
                t = _resolve_table(col, alias_map, tables)
                join_cols.setdefault(t or "unknown", set()).add(col.name)
        else:
            conditionless_joins += 1

    if conditionless_joins:
        # Recover the pairing predicates from WHERE. Only two-sided equalities
        # over two *different* table qualifiers qualify: `t.a = 5` is a filter,
        # and an unqualified operand is too ambiguous to call a join.
        where_node = parsed.find(exp.Where)
        cross_equalities = []
        if where_node is not None:
            for eq in where_node.find_all(exp.EQ):
                cols = list(eq.find_all(exp.Column))
                if len(cols) != 2:
                    continue
                left, right = cols
                if not left.table or not right.table or left.table == right.table:
                    continue
                cross_equalities.append(cols)

        # Every such equality marks its columns as join participants ...
        for cols in cross_equalities:
            for col in cols:
                t = _resolve_table(col, alias_map, tables)
                join_cols.setdefault(t or "unknown", set()).add(col.name)

        # ... but at most one entry per conditionless join, matching exactly
        # what a single `ON` clause of the same shape would have produced.
        for cols in cross_equalities[:conditionless_joins]:
            joins.append(_join_entry(cols, alias_map))

    order = parsed.find(exp.Order)
    if order:
        for col in order.find_all(exp.Column):
            t = _resolve_table(col, alias_map, tables)
            order_cols.setdefault(t or "unknown", set()).add(col.name)

    group = parsed.find(exp.Group)
    if group:
        for col in group.find_all(exp.Column):
            t = _resolve_table(col, alias_map, tables)
            group_cols_by_table.setdefault(t or "unknown", set()).add(col.name)

    return {
        "tables": tables, "alias_map": alias_map, "joins": joins,
        "where": where_cols, "join": join_cols, "order": order_cols,
        "group": group_cols_by_table, "select": select_cols,
    }


def _raw_order_by_clause(query: str) -> str:
    """
    Slice the ORDER BY clause out of the original query text.

    Stringifying the sqlglot AST normalises what the user typed — postgres
    `RANDOM()` renders as MySQL's `RAND()`, and sqlglot appends `NULLS LAST`
    — so a finding built from it would point at SQL that appears nowhere in
    the query. Quote the source instead; anything after the ordering list
    (LIMIT / OFFSET / FOR UPDATE) is not part of the clause.
    """
    match = re.search(r"\border\s+by\b.*$", query, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    tail = re.split(r"\blimit\b|\boffset\b|\bfor\s+update\b", match.group(0),
                    maxsplit=1, flags=re.IGNORECASE)[0]
    return tail.strip().rstrip(";").strip()


def detect_static_issues(query: str, dialect: str = "mysql") -> list:
    """
    Deterministic findings for constructs that cannot work — no LLM, no data
    and no statistics involved.

    The issue-finder is scope-restricted, and under the `joins` / `indexes`
    scopes it is explicitly told *"Do not discuss generic SQL structure
    issues"*, so a provably-broken construct comes back as `issues: []`.
    Observed on `ORDER BY (SELECT RANDOM())`: the response said "No
    optimizations were applied to the query" while the plan held no Sort node
    at all. These rules always run and close that gap.

    Each finding uses the same shape issue_finder_llm() emits so the planner
    consumes it unchanged, plus a `rule` key holding a lowercase substring the
    caller checks against model output — if the model already reported that
    topic the finding is dropped as a duplicate. Strip `rule` before planning.
    """
    try:
        parsed = sqlglot.parse_one(query, read=dialect)
    except ParseError:
        return []
    if parsed is None:
        return []

    findings = []
    order = parsed.find(exp.Order)
    if order is not None:
        outer_tables = {t.alias_or_name for t in parsed.find_all(exp.Table)}
        for item in order.expressions:
            sub = item.find(exp.Select)
            if sub is None:
                continue
            # An uncorrelated scalar subquery is evaluated exactly once — in
            # PostgreSQL it becomes an InitPlan producing a single value — so
            # every row receives the same sort key, nothing is ordered, and no
            # Sort node appears. A correlated subquery varies per row and is
            # legitimate, hence the outer-alias check.
            sub_tables = {t.alias_or_name for t in sub.find_all(exp.Table)}
            referenced_outer = {
                c.table for c in sub.find_all(exp.Column)
                if c.table and c.table in (outer_tables - sub_tables)
            }
            if referenced_outer:
                continue

            fragment = _raw_order_by_clause(query) or str(item).strip()
            findings.append({
                "rule": "order by",
                "scope": "sql_optimization",
                "title": (
                    "ORDER BY scalar subquery is evaluated once, so no ordering "
                    "is applied"
                ),
                "location": fragment,
                "impact": "LOW",
                "execution_time_cost": "SORT_COST",
                "root_cause": (
                    "The ORDER BY item is an uncorrelated scalar subquery: it "
                    "references no table from the outer query, so the engine "
                    "evaluates it a single time before rows are produced and "
                    "reuses that one value as the sort key for every row. All "
                    "rows therefore tie, no sort is performed, and the returned "
                    "row order is whatever the plan emitted. The clause is dead "
                    "code — if a random order is wanted it must be written as a "
                    "row-level expression, and if no order is wanted it can be "
                    "dropped outright."
                ),
                "evidence": (
                    f"query: {fragment} — the subquery selects from "
                    f"{sorted(sub_tables) or 'no tables'} and never mentions "
                    "an outer table, so it is a constant"
                ),
            })
    return findings


def _build_query_cols(meta: dict, table: str) -> set:
    cols = set()
    for key in ("where", "join", "order", "group", "select"):
        cols |= meta.get(key, {}).get(table, set())
    return cols


def _filter_indexes(indexes: list, query_cols: set, row_count) -> list:
    kept = []
    for idx in indexes:
        if idx.get("name") == "PRIMARY":
            kept.append(idx)
            continue
        if not any(c in query_cols for c in idx.get("cols", [])):
            continue
        sel = idx.get("selectivity")
        if sel is not None and sel < _MIN_INDEX_SELECTIVITY:
            continue
        kept.append(idx)
    return kept


def get_table_schema(cursor, table: str, meta: dict, row_count) -> dict:
    """Per-table schema: columns, roles, indexes w/ selectivity (mirrors agent_v3)."""
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
            columns.append({"name": col_name, "type": col_type,
                            "nullable": nullable == "YES"})

    avg_row_length, fragmentation_pct = None, None
    try:
        cursor.execute("SHOW TABLE STATUS LIKE %s", [table])
        ts_row = cursor.fetchone()
        if ts_row:
            # 0:Name 4:Rows 5:Avg_row_length 6:Data_length 8:Index_length 9:Data_free
            avg_row_length = ts_row[5]
            data_length  = ts_row[6] or 0
            index_length = ts_row[8] or 0
            data_free    = ts_row[9] or 0
            total = data_length + index_length + data_free
            if total > 0:
                fragmentation_pct = round((data_free / total) * 100, 2)
    except Exception:
        pass

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
                "name": idx, "cols": [], "unique": not bool(non_unique),
                "cardinality": card, "type": idx_type.lower(),
                "sub_parts": [], "nullable": [], "is_visible": True,
            }
        index_map[idx]["cols"].append(col)
        index_map[idx]["sub_parts"].append(sub_part)
        index_map[idx]["nullable"].append(nullable_idx == "YES")
        index_map[idx]["is_visible"] = (is_visible == "YES")

    raw_indexes = []
    for idx_data in index_map.values():
        card = idx_data.get("cardinality")
        if card and row_count and row_count > 0:
            idx_data["selectivity"] = round(card / row_count, 4)
        raw_indexes.append(idx_data)

    indexes = _filter_indexes(raw_indexes, relevant_cols, row_count)

    column_roles = {}
    for col in [c["name"] for c in columns]:
        roles = []
        if col in meta["where"].get(table, set()):            roles.append("filter")
        if col in meta["join"].get(table, set()):             roles.append("join")
        if col in meta["order"].get(table, set()):            roles.append("order")
        if col in meta["group"].get(table, set()):            roles.append("group")
        if col in meta.get("select", {}).get(table, set()):   roles.append("select")
        if roles:
            column_roles[col] = roles

    schema = {
        "row_count": row_count, "avg_row_length": avg_row_length,
        "columns": columns, "column_roles": column_roles, "indexes": indexes,
    }
    if fragmentation_pct is not None:
        schema["fragmentation_pct"] = fragmentation_pct
    return schema


def extract_schema(query: str, connection, database: str, engine: str = "mysql") -> tuple:
    """
    Extract the per-table schema for all tables referenced by `query`.
    Returns (tables_schema: dict, meta: dict). Individual table failures
    are swallowed (that table is omitted) - mirrors agent_v3 behaviour.
    Read-only: only INFORMATION_SCHEMA / SHOW TABLE STATUS / pg_catalog reads.

    `engine` selects the catalog dialect; the returned shape is identical so
    the LLM pipeline consumes either engine without changes.
    """
    is_pg = engine == "postgres"
    cursor = connection.cursor()
    meta = extract_query_metadata(query, dialect="postgres" if is_pg else "mysql")
    try:
        if is_pg:
            row_counts = _pg_estimated_row_counts(cursor, meta["tables"])
            getter = get_table_schema_postgres
        else:
            row_counts = _mysql_estimated_row_counts(cursor, meta["tables"])
            getter = get_table_schema

        tables_schema = {}
        for table in meta["tables"]:
            try:
                tables_schema[table] = getter(
                    cursor, table, meta, row_counts.get(table))
            except Exception:
                pass
        return tables_schema, meta
    finally:
        cursor.close()


def _mysql_estimated_row_counts(cursor, tables) -> dict:
    """Estimated row counts from INFORMATION_SCHEMA (MySQL)."""
    row_counts = {}
    for table in tables:
        try:
            cursor.execute("""
                SELECT TABLE_ROWS FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
            """, [table])
            r = cursor.fetchone()
            row_counts[table] = r[0] if r else None
        except Exception:
            row_counts[table] = None
    return row_counts


def _pg_estimated_row_counts(cursor, tables) -> dict:
    """
    Estimated row counts from pg_class.reltuples (PostgreSQL).

    reltuples is the planner's estimate - the direct analogue of MySQL's
    TABLE_ROWS. It is -1 on a table that has never been ANALYZEd, which is
    mapped to None so callers treat it as "unknown" rather than "empty".
    """
    row_counts = {}
    for table in tables:
        try:
            cursor.execute("""
                SELECT c.reltuples::bigint
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = %s AND n.nspname = current_schema()
            """, [table])
            r = cursor.fetchone()
            est = r[0] if r else None
            row_counts[table] = int(est) if est is not None and est >= 0 else None
        except Exception:
            row_counts[table] = None
    return row_counts


# ---------------------------------------------------------------------
# PostgreSQL schema extraction
# ---------------------------------------------------------------------
#
# Produces the exact same dict shape as get_table_schema() so the LLM
# pipeline and the response builder consume either engine unchanged.
#
# MySQL -> PostgreSQL mapping of the concepts involved:
#   COLUMN_TYPE            -> data_type (+ length/precision, see _pg_format_type)
#   TABLE_ROWS             -> pg_class.reltuples
#   AVG_ROW_LENGTH         -> pg_relation_size(relid) / reltuples
#   CARDINALITY            -> pg_stats.n_distinct (negative = fraction of rows)
#   INDEX_NAME 'PRIMARY'   -> any indisprimary index is reported as 'PRIMARY'
#   SUB_PART (prefix len)  -> not supported by PostgreSQL, always None
#   IS_VISIBLE             -> pg_index.indisvalid
#   fragmentation_pct      -> not derivable without pgstattuple, so omitted

_PG_INDEX_SQL = """
SELECT CASE WHEN ix.indisprimary THEN 'PRIMARY' ELSE i.relname END AS index_name,
       a.attname,
       ix.indisunique,
       am.amname,
       s.n_distinct,
       a.attnotnull,
       ix.indisvalid
FROM pg_index ix
JOIN pg_class i      ON i.oid = ix.indexrelid
JOIN pg_class t      ON t.oid = ix.indrelid
JOIN pg_namespace n  ON n.oid = t.relnamespace
JOIN pg_am am        ON am.oid = i.relam
JOIN LATERAL unnest(ix.indkey, ix.indoption) WITH ORDINALITY AS k(attnum, opt, ord) ON TRUE
JOIN pg_attribute a  ON a.attrelid = t.oid AND a.attnum = k.attnum
LEFT JOIN pg_stats s ON s.schemaname = n.nspname
                    AND s.tablename  = t.relname
                    AND s.attname    = a.attname
WHERE t.relname = %s AND n.nspname = current_schema() AND a.attnum > 0
ORDER BY index_name, k.ord
"""


def _pg_format_type(data_type, char_max_len, num_precision, num_scale) -> str:
    """
    Render a PostgreSQL column type with its size, the way MySQL's COLUMN_TYPE
    does. Native PostgreSQL names are preserved (e.g. "character varying(100)")
    rather than translated, so any DDL the rewriter emits stays valid.
    """
    if char_max_len is not None:
        return f"{data_type}({char_max_len})"
    if data_type == "numeric" and num_precision is not None:
        return f"numeric({num_precision},{num_scale if num_scale is not None else 0})"
    return data_type


def _pg_cardinality(n_distinct, row_count):
    """
    Convert pg_stats.n_distinct into an absolute distinct-value estimate.

    PostgreSQL stores a positive number as an absolute count and a negative
    number as a fraction of the table size (e.g. -1 means "unique column").
    """
    if n_distinct is None:
        return None
    if n_distinct > 0:
        return int(n_distinct)
    if not row_count:
        return None
    return max(1, int(round(abs(n_distinct) * row_count)))



def get_table_schema_postgres(cursor, table: str, meta: dict, row_count) -> dict:
    """Per-table schema for PostgreSQL: columns, roles, indexes w/ selectivity."""
    relevant_cols = _build_query_cols(meta, table)

    columns = []
    if relevant_cols:
        placeholders = ",".join(["%s"] * len(relevant_cols))
        cursor.execute(f"""
            SELECT column_name, data_type, character_maximum_length,
                   numeric_precision, numeric_scale, is_nullable
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name   = %s
              AND column_name IN ({placeholders})
            ORDER BY ordinal_position
        """, [table, *relevant_cols])
        for (col_name, data_type, char_len, num_prec, num_scale, nullable) in cursor.fetchall():
            columns.append({
                "name": col_name,
                "type": _pg_format_type(data_type, char_len, num_prec, num_scale),
                "nullable": nullable == "YES",
            })

    avg_row_length = None
    try:
        cursor.execute("""
            SELECT c.reltuples::bigint,
                   ROUND(pg_relation_size(c.oid) / NULLIF(c.reltuples, 0))::bigint
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = %s AND n.nspname = current_schema()
        """, [table])
        stats_row = cursor.fetchone()
        if stats_row:
            avg_row_length = stats_row[1]
    except Exception:
        pass

    cursor.execute(_PG_INDEX_SQL, [table])

    index_map = {}
    for (idx, col, is_unique, idx_type, n_distinct, not_null, is_valid) in cursor.fetchall():
        if idx not in index_map:
            index_map[idx] = {
                "name": idx, "cols": [], "unique": bool(is_unique),
                "cardinality": None, "type": (idx_type or "").lower(),
                "sub_parts": [], "nullable": [], "is_visible": True,
            }
        # The leading column drives the index's cardinality, mirroring how
        # MySQL reports CARDINALITY for an index prefix.
        if not index_map[idx]["cols"]:
            index_map[idx]["cardinality"] = _pg_cardinality(n_distinct, row_count)
        index_map[idx]["cols"].append(col)
        index_map[idx]["sub_parts"].append(None)  # no prefix indexes in PostgreSQL
        index_map[idx]["nullable"].append(not not_null)
        index_map[idx]["is_visible"] = bool(is_valid)

    raw_indexes = []
    for idx_data in index_map.values():
        card = idx_data.get("cardinality")
        if card and row_count and row_count > 0:
            idx_data["selectivity"] = round(card / row_count, 4)
        raw_indexes.append(idx_data)

    indexes = _filter_indexes(raw_indexes, relevant_cols, row_count)

    column_roles = {}
    for col in [c["name"] for c in columns]:
        roles = []
        if col in meta["where"].get(table, set()):            roles.append("filter")
        if col in meta["join"].get(table, set()):             roles.append("join")
        if col in meta["order"].get(table, set()):            roles.append("order")
        if col in meta["group"].get(table, set()):            roles.append("group")
        if col in meta.get("select", {}).get(table, set()):   roles.append("select")
        if roles:
            column_roles[col] = roles

    return {
        "row_count": row_count, "avg_row_length": avg_row_length,
        "columns": columns, "column_roles": column_roles, "indexes": indexes,
    }



# ---------------------------------------------------------------------
# Query-table resolution (is the table actually visible to this connection?)
# ---------------------------------------------------------------------
#
# extract_schema() deliberately mirrors agent_v3.py and swallows per-table
# failures, so a query whose tables live in a *different* schema than the one
# the connection defaults to yields an empty-but-successful schema: row_count
# None, columns [], indexes [].
#
# That silence is misleading, and the common trigger is identifier case.
# sqlglot reads the query text only - it never touches the database - so
# `query_tables` can happily list a table this connection cannot see. On a
# Linux MySQL server with lower_case_table_names=0 (the Aiven default),
# `chinook` and `Chinook` are two distinct schemas; connecting to the wrong
# one leaves the tables invisible while the connection itself succeeds.
#
# The probe below compares names case-insensitively on purpose: that lets it
# say *which* schema actually holds the table instead of just reporting a miss.

def _current_schema(cursor, engine: str):
    """Name of the schema this connection resolves unqualified tables in."""
    if engine == "postgres":
        cursor.execute("SELECT current_schema()")
    else:
        cursor.execute("SELECT DATABASE()")
    row = cursor.fetchone()
    return row[0] if row else None


def _identifier_case(cursor, engine: str) -> tuple:
    """
    (identifier_case_is_significant, note) for this server.

    MySQL: significant only when lower_case_table_names=0 (the Unix/Aiven
    default) - with 1 or 2 the server folds names itself, so a spelling
    difference is harmless.
    PostgreSQL: unquoted identifiers fold to lower case, so a difference from
    that folding is legal. Quoted identifiers are case-sensitive, but sqlglot
    does not preserve the quoting by the time we see the name, and warning
    about a query the server accepts would be worse than staying quiet.
    """
    if engine == "postgres":
        return False, "PostgreSQL folds unquoted identifiers to lower case"
    try:
        cursor.execute("SELECT @@lower_case_table_names")
        row = cursor.fetchone()
        lctn = int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return False, ("identifier case follows the server's "
                       "lower_case_table_names setting")
    if lctn == 0:
        return True, ("lower_case_table_names=0 - schema and table names are "
                      "case-sensitive on this server")
    return False, (f"lower_case_table_names={lctn} - schema and table names "
                   "are compared case-insensitively")


def _probe_table_locations(cursor, table: str, engine: str) -> list:
    """Every (schema, table) pair matching `table` case-insensitively."""
    if engine == "postgres":
        cursor.execute(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE LOWER(table_name) = LOWER(%s)
            ORDER BY (table_schema = current_schema()) DESC, table_schema
            """,
            [table],
        )
    else:
        cursor.execute(
            """
            SELECT TABLE_SCHEMA, TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE LOWER(TABLE_NAME) = LOWER(%s)
            ORDER BY (TABLE_SCHEMA = DATABASE()) DESC, TABLE_SCHEMA
            """,
            [table],
        )
    return [(row[0], row[1]) for row in cursor.fetchall()]


def resolve_query_tables(cursor, tables, engine: str = "mysql") -> tuple:
    """
    Check which of the query's tables the connection can actually see.

    Returns (unresolved, resolved):
      unresolved -- {table: reason} for tables absent from the current schema,
                    each reason carrying an actionable suggestion
      resolved   -- [table, ...] present in the connection's default schema

    Read-only: INFORMATION_SCHEMA / information_schema reads only. A catalog
    failure for one table is reported as that table's reason rather than
    raised, so it can never break extraction itself.
    """
    unresolved, resolved = {}, []

    try:
        current = _current_schema(cursor, engine)
    except Exception:
        current = None

    try:
        case_sensitive, case_note = _identifier_case(cursor, engine)
    except Exception:
        case_sensitive, case_note = False, "identifier case handling is unknown"

    for table in sorted(tables):
        try:
            locations = _probe_table_locations(cursor, table, engine)
        except Exception as exc:
            unresolved[table] = (
                f"Could not verify table '{table}' against the catalog: "
                f"{type(exc).__name__}"
            )
            continue

        in_current = [(schema, name) for schema, name in locations
                      if schema == current]
        if any(name == table for _, name in in_current):
            resolved.append(table)
            continue

        if in_current:
            # Right schema, only the spelling differs. Harmless on a server
            # that folds identifiers itself; a hard error on one that does not
            # (`FROM Orders` against a table named `orders`).
            if not case_sensitive:
                resolved.append(table)
                continue
            unresolved[table] = (
                f"Table '{table}' does not exist in schema '{current}'; a "
                f"table named {sorted({name for _, name in in_current})} does. "
                f"{case_note} - match the stored table-name spelling exactly."
            )
            continue

        # Not in the connected schema at all. This is the case that used to
        # pass silently: the connection succeeds, every catalog read returns
        # nothing, and the schema comes back empty with no explanation.
        same_spelling = sorted({schema for schema, name in locations
                                if name == table})
        if same_spelling:
            # The table exists - the connection is simply pointed at a
            # differently-cased schema name.
            unresolved[table] = (
                f"Table '{table}' is not visible in the connected schema "
                f"'{current}' but exists in schema(s) {same_spelling}. "
                f"{case_note} - connect to that exact schema name "
                f"(the request's database_config.database)."
            )
        elif locations:
            unresolved[table] = (
                f"Table '{table}' was not found in schema '{current}'. "
                f"A case-insensitive match exists in schema(s) "
                f"{sorted({schema for schema, _ in locations})} as "
                f"{sorted({name for _, name in locations})}. "
                f"Check both the schema name and the table-name spelling."
            )
        else:
            unresolved[table] = (
                f"Table '{table}' was not found in any schema this user can "
                f"read (connected schema: '{current}')."
            )

    return unresolved, resolved

