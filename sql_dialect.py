"""
SQL dialect awareness for multi-engine support.

Both the optimization prompts and the output guard need to know which engine the
target database runs, because a construct that is valid on one engine is a hard
syntax error on the other — most visibly MySQL's ``FORCE INDEX`` hint, which
PostgreSQL cannot parse at all.

Dependencies are deliberately light: regexes plus sqlglot, which is already a
project dependency via validator/query_validator.py.
"""

import logging
import re
from typing import List, Optional, Tuple

import sqlglot
from sqlglot.errors import ParseError

log = logging.getLogger("query_optimizer")

MYSQL = "mysql"
POSTGRES = "postgres"

#: Human/LLM-facing engine name.
DIALECT_NAMES = {
    MYSQL: "MySQL 8.0",
    POSTGRES: "PostgreSQL 16",
}

#: sqlglot dialect id per engine.
SQLGLOT_DIALECTS = {
    MYSQL: "mysql",
    POSTGRES: "postgres",
}

_MYSQL_RULES = """━━━ MySQL 8.0 SYNTAX RULES ━━━
- Index hints are allowed and often useful: FROM t USE INDEX (idx), FORCE INDEX (idx), IGNORE INDEX (idx).
- Implicit joins are valid: FROM a, b WHERE a.id = b.id.
- Backtick-quoted identifiers are valid: `col`.
- LIMIT offset, count is valid.
- Use DATE_FORMAT, IFNULL, GROUP_CONCAT, NOW(), CONCAT.
- NEVER emit PostgreSQL-only syntax: :: casts, ILIKE, DISTINCT ON, LATERAL, STRING_AGG, GENERATE_SERIES."""

_PG_RULES = """━━━ PostgreSQL 16 SYNTAX RULES ━━━
- INDEX HINTS DO NOT EXIST. NEVER emit USE INDEX, FORCE INDEX, IGNORE INDEX, FORCE KEY or STRAIGHT_JOIN — every one of them is a hard syntax error. PostgreSQL picks access paths from its own statistics; to change them, rewrite the predicate (make it sargable) or recommend an index via CREATE INDEX instead.
- Cast with CAST(x AS type) or the :: operator.
- Use COALESCE, STRING_AGG, TO_CHAR, CURRENT_TIMESTAMP, CONCAT.
- LIMIT count OFFSET start. The MySQL "LIMIT offset, count" form is invalid.
- Double-quote identifiers ("col"). Backticks and ZEROFILL are invalid.
- NEVER emit MySQL-only syntax: backticks, IFNULL, DATE_FORMAT, GROUP_CONCAT, SQL_CALC_FOUND_ROWS, SQL_NO_CACHE, UNSIGNED.
- A bare column comparison is preferable to wrapping the column in a function, but do NOT drop a case-folding function such as lower(col) when the column's collation is case-sensitive — that would change which rows match."""


def dialect_name(engine: Optional[str]) -> str:
    """Human/LLM-facing name for `engine` (defaults to MySQL)."""
    return DIALECT_NAMES.get((engine or MYSQL).strip().lower(), DIALECT_NAMES[MYSQL])


def syntax_rules(engine: Optional[str]) -> str:
    """Engine-specific syntax rules block for the LLM prompts."""
    if (engine or MYSQL).strip().lower() == POSTGRES:
        return _PG_RULES
    return _MYSQL_RULES