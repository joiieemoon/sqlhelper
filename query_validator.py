import sqlglot
from sqlglot.errors import ParseError
from sqlglot import expressions as exp

MAX_QUERY_LENGTH = 8000  # tune to your realistic max

# sqlglot dialect per supported engine. This guard is the only place that needs
# to know the target dialect: MySQL and PostgreSQL differ in syntax (e.g. `::`
# casts), so parsing a PostgreSQL query with the MySQL parser would reject it
# before any safety check ran.
_DIALECTS = {
    "mysql": "mysql",
    "mariadb": "mysql",
    "postgres": "postgres",
    "postgresql": "postgres",
    "pg": "postgres",
}


def validate_query(query, dialect="mysql"):
    if not query or not query.strip():
        return "Error: Empty query."

    if len(query) > MAX_QUERY_LENGTH:
        return f"Error: Query exceeds max length of {MAX_QUERY_LENGTH} characters."

    read_dialect = _DIALECTS.get(str(dialect).strip().lower(), "mysql")
    try:
        statements = sqlglot.parse(query, read=read_dialect)
    except ParseError:
        return "Error: Only SQL SELECT queries are allowed."

    # reject multi-statement input outright — this is the critical fix
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return "Error: Only a single SELECT statement is allowed."

    parsed = statements[0]

    if not isinstance(parsed, exp.Select):
        return "Error: Only SELECT queries are allowed."

    # reject disallowed node types anywhere in the tree
    # (belt-and-suspenders against constructs you don't want reaching the LLM/DB)
    disallowed = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter,
                  exp.Create, exp.Command, exp.Merge)
    for node in parsed.walk():
        if isinstance(node, disallowed):
            return "Error: Disallowed statement type detected inside query."

    return "FINE"