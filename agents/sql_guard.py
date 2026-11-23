"""SQL guard: the check every agent-written query must pass before it reaches Postgres.

It parses the SQL into a syntax tree with sqlglot (instead of searching the text for words like
"DELETE", which is easy to fool) and only lets through:
- exactly one statement, and it's a read (SELECT, optionally with CTEs or UNION),
- that reads only the allowlisted marts tables (no raw/comp/ops, no pg_catalog or
  information_schema, so "show me every table" has nothing to show),
- that calls no dangerous functions (pg_sleep, pg_read_file, set_config, dblink, ...),
- and it always ends with a LIMIT of at most MAX_ROWS (added or lowered if needed).

This is one layer of several. Behind it: the read-only `agent_ro` login (SELECT on marts only),
read-only transactions, and a 5-second statement timeout. Any layer alone should stop a bad query.
"""

import logging

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# sqlglot warns when it falls back to a generic "Command" (e.g. SET ROLE). We reject those
# anyway, so the warning is just noise in the logs.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

MAX_ROWS = 200

# The only tables the agent may read (all in the marts schema). Unqualified names are checked
# against this list too, because Postgres always searches pg_catalog first: an unqualified
# "pg_roles" would otherwise quietly read a system table.
ALLOWED_TABLES = frozenset({"fct_deals", "dim_reps", "fct_forecast_runs"})
ALLOWED_SCHEMA = "marts"

# Any of these anywhere in the tree means the query writes, changes settings or locks rows.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    node
    for name in (
        "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "TruncateTable",
        "Command", "Set", "Copy", "Grant", "Into", "Lock", "Transaction", "Commit", "Rollback",
    )
    if isinstance(node := getattr(exp, name, None), type)
)  # fmt: skip

# Function names (lowercase) that are never allowed, plus any pg_* / lo_* admin function.
BLOCKED_FUNCTIONS = frozenset(
    {"dblink", "set_config", "current_setting", "query_to_xml", "copy", "version"}
)
BLOCKED_PREFIXES = ("pg_", "lo_", "dblink")


class UnsafeSQLError(ValueError):
    """The query was rejected. The message says why, in words the model can act on."""


def function_name(node: exp.Func) -> str:
    """Lowercase function name: Anonymous nodes keep the name as written, known ones have one."""
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return node.sql_name().lower()


def validate_select(query: str) -> str:
    """Return a safe version of `query` (with a LIMIT), or raise UnsafeSQLError."""
    try:
        statements = [s for s in sqlglot.parse(query, read="postgres") if s is not None]
    except ParseError as exc:
        first_line = str(exc).splitlines()[0]
        raise UnsafeSQLError(f"Could not parse the SQL: {first_line}") from exc

    if len(statements) != 1:
        raise UnsafeSQLError("Send exactly one SQL statement.")
    tree = statements[0]

    if not isinstance(tree, exp.Select | exp.SetOperation):
        raise UnsafeSQLError("Only SELECT queries are allowed (read-only access).")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeSQLError(f"{type(node).__name__} is not allowed: read-only access.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name, schema = table.name.lower(), table.db.lower()
        if table.catalog:
            raise UnsafeSQLError(f"Cross-database references are not allowed: {table.sql()}")
        if not schema and name in cte_names:
            continue  # a CTE defined in this query
        if schema not in ("", ALLOWED_SCHEMA) or name not in ALLOWED_TABLES:
            allowed = ", ".join(f"{ALLOWED_SCHEMA}.{t}" for t in sorted(ALLOWED_TABLES))
            raise UnsafeSQLError(f"Table {table.sql()} is not available. Use only: {allowed}.")

    for func in tree.find_all(exp.Func):
        name = function_name(func)
        if name in BLOCKED_FUNCTIONS or name.startswith(BLOCKED_PREFIXES):
            raise UnsafeSQLError(f"Function {name}() is not allowed.")

    return with_row_limit(tree).sql(dialect="postgres")


def with_row_limit(tree: exp.Select | exp.SetOperation) -> exp.Expression:
    """Add LIMIT MAX_ROWS, or lower an existing LIMIT that's bigger (or not a plain number)."""
    limit = tree.args.get("limit")
    if limit is not None:
        value = limit.expression
        if isinstance(value, exp.Literal) and value.is_int and int(value.this) <= MAX_ROWS:
            return tree
    return tree.limit(MAX_ROWS)
