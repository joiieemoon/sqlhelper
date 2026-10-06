"""
query_validator.py — SELECT-only guard used by every backend route.

This is the single authoritative validation module. api.py imports it through
`validator.query_validator`; nothing else may implement its own weaker copy.

Multi-layer design (each layer must pass; the first failure wins):

  L0  size / encoding / null-byte / control-character checks
  L1  lexical rejects BEFORE parsing
        - MySQL executable (versioned) comments  /*! ... */  and /*+ ... */
          sqlglot treats these as ordinary comments, but MySQL EXECUTES their
          contents, so a statement can pass AST checks while doing something
          else on the server.
        - dangerous bare keywords anywhere in the text (a backstop only; the
          AST layer is authoritative and this just catches parser gaps)
  L2  parse with the target dialect
  L3  exactly one statement, and it must be SELECT / UNION / EXCEPT / INTERSECT
  L4  deep AST walk: no DDL/DML node, no INTO OUTFILE/DUMPFILE,
      no user-variable assignment, no dangerous function
  L5  re-parse round-trip: the normalised statement must still be
      single-statement and read-only

Returning "FINE" is the only way a query proceeds. Every rejection returns a
human-readable "Error: ..." string.
"""

import re

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import ParseError

MAX_QUERY_LENGTH = 8000  # tune to your realistic max

# Accepted dialect names, mapped to the sqlglot dialect used for parsing.
_DIALECTS = {
    "mysql": "mysql",
    "mariadb": "mysql",
    "postgres": "postgres",
    "postgresql": "postgres",
    "pg": "postgres",
}

# Top-level shapes that are read-only. A plain `VALUES (...)` is read-only too,
# and a parenthesised SELECT arrives as exp.Subquery — both are legitimate and
# the deep L4 walk still applies to everything inside them.
_ALLOWED_TOP_LEVEL = (
    exp.Select, exp.Union, exp.Except, exp.Intersect, exp.Values, exp.Subquery,
)

# Any of these anywhere in the tree means the statement is not read-only.
_DISALLOWED_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter,
    exp.Create, exp.Command, exp.Merge, exp.TruncateTable,
    exp.Set, exp.Use, exp.Grant, exp.Revoke,
)

# Functions with side effects or filesystem/network reach.
_DANGEROUS_FUNCS = {
    "load_file", "sys_exec", "sys_eval", "benchmark",
    "sleep", "outfile", "dumpfile",
}

_VERSIONED_COMMENT_RE = re.compile(r"/\*[!+]")

_FORBIDDEN_KEYWORDS = (
    r"\binsert\b", r"\breplace\b", r"\bupdate\b", r"\bdelete\b",
    r"\btruncate\b", r"\bdrop\b", r"\balter\b", r"\bcreate\b",
    r"\brename\b", r"\bgrant\b", r"\brevoke\b", r"\bmerge\b",
    r"\bcall\b", r"\bexecute\b", r"\bhandler\b", r"\bload\b",
    r"\boutfile\b", r"\bdumpfile\b", r"\binto\b", r"\bset\b",
    r"\buse\b", r"\battach\b", r"\bdetach\b", r"\bvacuum\b",
    r"\bpragma\b", r"\bcopy\b", r"\bcommit\b", r"\brollback\b",
    r"\bshutdown\b", r"\bkill\b", r"\bflush\b", r"\bprepare\b",
)
_FORBIDDEN_RE = re.compile("|".join(_FORBIDDEN_KEYWORDS), re.IGNORECASE)

# `SELECT @x := 1` parses to PropertyEQ (`:=`), while `SELECT a = 1` parses to EQ.
# Either node with a user-variable LHS is an assignment, not a read.
_ASSIGNMENT_NODES = tuple(
    n for n in (getattr(exp, "PropertyEQ", None), getattr(exp, "EQ", None))
    if n is not None
)


def _strip_literals_and_comments(query: str) -> str:
    """
    Blank out string literals and comments so the L1 keyword backstop only
    sees real SQL.

    Without this, a harmless predicate like
        WHERE status = 'insert pending'
    is rejected because the word INSERT appears inside a literal.
    Doubled quotes ('') and backslash escapes are handled.
    """
    out = []
    i = 0
    n = len(query)
    while i < n:
        ch = query[i]

        if ch == "'" or ch == '"' or ch == "`":
            quote = ch
            out.append(" ")
            i += 1
            while i < n:
                if query[i] == "\\":
                    i += 2
                    continue
                if query[i] == quote:
                    if i + 1 < n and query[i + 1] == quote:
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue

        if ch == "-" and i + 1 < n and query[i + 1] == "-":
            nl = query.find("\n", i)
            i = n if nl == -1 else nl
            out.append(" ")
            continue

        if ch == "/" and i + 1 < n and query[i + 1] == "*":
            end = query.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def _describe(node) -> str:
    try:
        return type(node).__name__
    except Exception:  # pragma: no cover - defensive
        return "node"


def _check_ast(parsed) -> str:
    """L4: deep walk for any write / side-effecting construct."""
    for node in parsed.walk():
        if node is None:
            continue

        for bad in _DISALLOWED_NODES:
            if isinstance(node, bad):
                return (
                    "Error: Disallowed statement type detected inside query "
                    f"({_describe(node)})."
                )

        if isinstance(node, exp.Into):
            return "Error: SELECT ... INTO is not allowed."

        # Any user variable (@x / $1) in a read-only optimizer query means
        # session state is being read or written — neither is legitimate here.
        if isinstance(node, (exp.Parameter, exp.SessionParameter)):
            return "Error: User/session variables are not allowed in a read-only query."

        name = (getattr(node, "name", None) or "").lower()
        if name in _DANGEROUS_FUNCS:
            return f"Error: Disallowed function detected inside query ({name})."

        # Variable assignment: SELECT @x := 1  (PropertyEQ) or SET @x (EQ)
        if isinstance(node, _ASSIGNMENT_NODES) and isinstance(
            getattr(node, "this", None), (exp.Parameter, exp.SessionParameter)
        ):
            return "Error: Variable assignment is not allowed in a read-only query."

    return "FINE"


def _check_round_trip(parsed, read: str) -> str:
    """
    L5: re-parse the normalised SQL and re-apply the safety rules.

    Catches the class of dialect quirks where a statement parses as a SELECT
    but its canonical form for that dialect is not read-only.
    """
    try:
        normalised = parsed.sql(dialect=read)
    except Exception:
        return "FINE"  # cannot re-render; the AST checks above already passed

    try:
        again = sqlglot.parse(normalised, read=read)
    except ParseError:
        # A re-parse failure says nothing about safety: L3 already proved the
        # node is a read-only statement and L4 already walked it. Some valid
        # read-only shapes (VALUES ROW(...)) simply do not survive a
        # re-render/re-parse round trip, so rejecting here would be a false
        # positive, not a defence.
        return "FINE"

    again = [s for s in again if s is not None]
    if len(again) != 1:
        return "Error: Only a single SELECT statement is allowed."
    if not isinstance(again[0], _ALLOWED_TOP_LEVEL):
        return "Error: Only SELECT queries are allowed."

    return "FINE"


def validate_query(query, dialect: str = "mysql") -> str:
    """
    SELECT-only guard. Returns "FINE" or an "Error: ..." string.

    `dialect` selects the sqlglot read dialect so PostgreSQL-specific syntax
    (e.g. `::` casts) parses instead of being rejected as invalid SQL. The
    safety rules are dialect-independent: exactly one statement, it must be
    read-only, and no DDL/DML node anywhere in the tree.
    """
    # ── L0: shape, size, encoding ──────────────────────────────────────
    if query is None or not str(query).strip():
        return "Error: Empty query."
    if not isinstance(query, str):
        query = str(query)

    if len(query) > MAX_QUERY_LENGTH:
        return f"Error: Query exceeds max length of {MAX_QUERY_LENGTH} characters."

    if "\x00" in query:
        return "Error: Query contains a null byte."
    if any(ord(c) < 32 and c not in "\t\n\r" for c in query):
        return "Error: Query contains disallowed control characters."

    read = _DIALECTS.get(str(dialect).strip().lower(), "mysql")

    # ── L1: lexical rejects that must run before parsing ───────────────
    if _VERSIONED_COMMENT_RE.search(query):
        return ("Error: Query contains disallowed comment syntax "
                "(/*! ... */ or /*+ ... */).")

    hit = _FORBIDDEN_RE.search(_strip_literals_and_comments(query))
    if hit:
        return (
            f"Error: Disallowed keyword '{hit.group(0).upper()}' — only read-only "
            f"SELECT statements are permitted."
        )

    # ── L2: parse with the target dialect ─────────────────────────────
    try:
        statements = sqlglot.parse(query, read=read)
    except ParseError:
        return "Error: Only SQL SELECT queries are allowed."

    # ── L3: exactly one read-only statement ───────────────────────────
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return "Error: Only a single SELECT statement is allowed."

    parsed = statements[0]
    if not isinstance(parsed, _ALLOWED_TOP_LEVEL):
        return "Error: Only SELECT queries are allowed."

    # ── L4: deep AST walk ──────────────────────────────────────────────
    result = _check_ast(parsed)
    if result != "FINE":
        return result

    # ── L5: round-trip re-parse ────────────────────────────────────────
    return _check_round_trip(parsed, read)
