"""MCP Tools — categorized SQL operations with elicitation for destructive actions.

Categories:
    - create_table   : DDL → CREATE TABLE only
    - alter_sql      : DDL → ALTER TABLE only
    - update_sql     : DML → UPDATE only (build-or-pass-raw)
    - delete_sql     : DML/DDL → DELETE / TRUNCATE / DROP TABLE (always confirms)
    - insert_sql     : DML → INSERT only
    - select_query   : DQL → SELECT (returns rows)
    - describe_table : metadata → column info
    - explain_query  : optimizer plan (EXPLAIN)
    - explain_analyze: real execution plan + timing (EXPLAIN ANALYZE)
"""

import json
import logging
import re
from typing import Awaitable, Callable

from mcp.server.fastmcp import Context
from pydantic import BaseModel, Field

from db import QueryResult, check_injection, validate_identifier

logger = logging.getLogger("db-mcp-server")


# ---------------------------------------------------------------------------
# Elicitation schemas
# ---------------------------------------------------------------------------


class ConfirmAction(BaseModel):
    confirm: bool = Field(description="Set to true to confirm this operation")


class ConfirmDrop(BaseModel):
    confirm: bool = Field(description="Set to true to confirm this destructive operation")
    type_to_confirm: str = Field(description="Type 'DROP' to confirm deletion")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_result(result: QueryResult, query: str) -> str:
    payload: dict = {
        "status": "success",
        "query": query,
        "rows_affected": result.row_count,
        "execution_time_ms": result.execution_time_ms,
    }
    if result.rows:
        payload["data"] = result.rows
    return json.dumps(payload, indent=2, default=str)


def _error(message: str, **extra) -> str:
    return json.dumps({"status": "error", "message": message, **extra}, indent=2, default=str)


def _starts_with(query: str, *keywords: str) -> bool:
    stripped = query.strip().upper()
    return any(stripped.startswith(kw) for kw in keywords)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

ExecuteFn = Callable[[str, str | None], Awaitable[QueryResult]]


def register_tools(mcp, execute: ExecuteFn, default_database: str) -> None:
    """Register all categorized SQL tools."""

    # -----------------------------------------------------------------------
    # CREATE TABLE — DDL
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def create_table(
        ctx: Context,
        table_name: str,
        columns: str,
        database: str | None = None,
        if_not_exists: bool = True,
    ) -> str:
        """
        Create a new table. ONLY accepts CREATE TABLE operations.

        Use this when the user asks to create / add / make a new table.

        Args:
            table_name: Identifier of the table to create (letters, digits, underscores).
            columns: Comma-separated column definitions.
                     Example: "id INT PRIMARY KEY AUTO_INCREMENT, name VARCHAR(255) NOT NULL, age INT"
            database: Target database. Omit to use the configured default.
            if_not_exists: Include 'IF NOT EXISTS' clause. Default True.
        """
        db = database or default_database
        logger.debug("[create_table] table=%s db=%s", table_name, db)

        if err := validate_identifier(table_name):
            return _error(err)
        if err := check_injection(columns):
            return _error(err)

        prefix = "IF NOT EXISTS " if if_not_exists else ""
        query = f"CREATE TABLE {prefix}{table_name} ({columns})"

        try:
            result = await execute(query, db)
            logger.info("[create_table] ✔ Created table '%s' in db='%s'", table_name, db)
            return _format_result(result, query)
        except Exception as e:
            logger.error("[create_table] ✗ Failed: %s", e)
            return _error(str(e), query=query)
    @mcp.tool()
    async def alter_sql(
        ctx: Context,
        query: str,
        database: str | None = None,
    ) -> str:
        """
        Modify the structure of an existing table. ONLY accepts ALTER TABLE statements.

        Use this for: ADD COLUMN, DROP COLUMN, MODIFY COLUMN, RENAME COLUMN,
        ADD/DROP CONSTRAINT, ADD INDEX, etc.

        Elicits user confirmation when the operation contains DROP.

        Args:
            query: Full ALTER TABLE statement.
                   Example: "ALTER TABLE users ADD COLUMN age INT"
                   Example: "ALTER TABLE orders DROP COLUMN legacy_id"
            database: Target database. Omit to use the configured default.
        """
        db = database or default_database
        logger.debug("[alter_sql] db=%s query=%s", db, query)

        if not _starts_with(query, "ALTER"):
            return _error("Only ALTER statements are allowed with this tool. Use the appropriate tool for other operations.")
        if err := check_injection(query):
            return _error(err)

        if re.search(r"\bDROP\b", query, re.IGNORECASE):
            resp = await ctx.elicit(
                message=f"⚠️ ALTER contains DROP:\n```sql\n{query}\n```\nThis is irreversible. Confirm?",
                schema=ConfirmAction,
            )
            if resp.action != "accept" or not resp.data or not resp.data.confirm:
                logger.warning("[alter_sql] User cancelled ALTER with DROP")
                return _error("ALTER cancelled by user.", status="cancelled")

        try:
            result = await execute(query, db)
            logger.info("[alter_sql] ✔ Executed: %s", query[:100])
            return _format_result(result, query)
        except Exception as e:
            logger.error("[alter_sql] ✗ Failed: %s", e)
            return _error(str(e), query=query)

    # -----------------------------------------------------------------------
    # UPDATE — DML
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def update_sql(
        ctx: Context,
        table_name: str | None = None,
        set_clause: str | None = None,
        where_clause: str | None = None,
        query: str | None = None,
        database: str | None = None,
    ) -> str:
        """
        Update existing rows in a table. ONLY accepts UPDATE operations.

        Two ways to call this tool:

        1) Pass a complete `query`:
           query="UPDATE users SET status='active' WHERE id=5"

        2) Pass parts and the query is built:
           table_name="users", set_clause="status='active'", where_clause="id=5"
           - If `where_clause` is omitted, the UPDATE will affect ALL rows (extra confirmation required).

        Requires user confirmation before execution.

        Args:
            table_name: Table to update (used when `query` is not provided).
            set_clause: SET expression without the SET keyword. Example: "name='Jane', age=25".
            where_clause: WHERE expression without the WHERE keyword. Example: "id=3".
                          Omit to update all rows (will trigger a stronger confirmation).
            query: Full UPDATE statement. If provided, table_name/set_clause/where_clause are ignored.
            database: Target database. Omit to use the configured default.
        """
        db = database or default_database

        # Build the final query
        if query:
            final_query = query
        else:
            if not table_name or not set_clause:
                return _error("Provide either `query` OR both `table_name` and `set_clause`.")
            if err := validate_identifier(table_name):
                return _error(err)
            if err := check_injection(set_clause):
                return _error(err)
            if where_clause and (err := check_injection(where_clause)):
                return _error(err)
            final_query = f"UPDATE {table_name} SET {set_clause}"
            if where_clause:
                final_query += f" WHERE {where_clause}"

        logger.debug("[update_sql] db=%s query=%s", db, final_query)

        if not _starts_with(final_query, "UPDATE"):
            return _error("Only UPDATE statements are allowed with this tool.")
        if err := check_injection(final_query):
            return _error(err)

        # No WHERE clause = mass update → stronger confirmation
        has_where = re.search(r"\bWHERE\b", final_query, re.IGNORECASE)
        if not has_where:
            resp = await ctx.elicit(
                message=f"🚨 UPDATE without WHERE will affect EVERY row:\n```sql\n{final_query}\n```\nSet confirm=true and type 'DROP' to proceed.",
                schema=ConfirmDrop,
            )
            if resp.action != "accept" or not resp.data:
                logger.warning("[update_sql] User cancelled UPDATE (no WHERE)")
                return _error("UPDATE cancelled.", status="cancelled")
            if not resp.data.confirm or resp.data.type_to_confirm != "DROP":
                logger.warning("[update_sql] User failed confirmation (no WHERE)")
                return _error("Confirmation failed.", status="cancelled")
        else:
            resp = await ctx.elicit(
                message=f"Confirm UPDATE:\n```sql\n{final_query}\n```\nProceed?",
                schema=ConfirmAction,
            )
            if resp.action != "accept" or not resp.data or not resp.data.confirm:
                logger.warning("[update_sql] User cancelled UPDATE")
                return _error("UPDATE cancelled.", status="cancelled")

        try:
            result = await execute(final_query, db)
            logger.info("[update_sql] ✔ Executed: %s", final_query[:100])
            return _format_result(result, final_query)
        except Exception as e:
            logger.error("[update_sql] ✗ Failed: %s", e)
            return _error(str(e), query=final_query)

    # -----------------------------------------------------------------------
    # DELETE / TRUNCATE / DROP — destructive
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def delete_sql(
        ctx: Context,
        query: str,
        database: str | None = None,
    ) -> str:
        """
        Delete data or drop a table. ONLY accepts DELETE / TRUNCATE / DROP TABLE statements.

        Use this for:
            - DELETE FROM table WHERE ...
            - TRUNCATE TABLE table
            - DROP TABLE table

        Always requires user confirmation. DROP / TRUNCATE require typing 'DROP' to confirm.

        Args:
            query: Full DELETE / TRUNCATE / DROP TABLE statement.
                   Examples:
                       "DELETE FROM users WHERE id=5"
                       "TRUNCATE TABLE sessions"
                       "DROP TABLE old_logs"
            database: Target database. Omit to use the configured default.
        """
        db = database or default_database
        logger.debug("[delete_sql] db=%s query=%s", db, query)

        if not _starts_with(query, "DELETE", "TRUNCATE", "DROP"):
            return _error("Only DELETE, TRUNCATE, or DROP TABLE statements are allowed with this tool.")

        # Block destructive forms beyond DROP TABLE
        if _starts_with(query, "DROP") and not re.match(r"\s*DROP\s+TABLE\b", query, re.IGNORECASE):
            return _error("Only DROP TABLE is allowed here. Use database admin tools for DROP DATABASE/SCHEMA/INDEX.")

        if err := check_injection(query):
            return _error(err)

        # DROP/TRUNCATE or DELETE without WHERE → strict confirmation
        is_drop_or_truncate = _starts_with(query, "DROP", "TRUNCATE")
        delete_without_where = _starts_with(query, "DELETE") and not re.search(r"\bWHERE\b", query, re.IGNORECASE)

        if is_drop_or_truncate or delete_without_where:
            resp = await ctx.elicit(
                message=f"🚨 DESTRUCTIVE — data will be permanently lost:\n```sql\n{query}\n```\nSet confirm=true and type 'DROP' to proceed.",
                schema=ConfirmDrop,
            )
            if resp.action != "accept" or not resp.data:
                logger.warning("[delete_sql] User cancelled destructive operation")
                return _error("Cancelled.", status="cancelled")
            if not resp.data.confirm or resp.data.type_to_confirm != "DROP":
                logger.warning("[delete_sql] User failed confirmation for destructive operation")
                return _error("Confirmation failed.", status="cancelled")
        else:
            resp = await ctx.elicit(
                message=f"⚠️ Confirm DELETE:\n```sql\n{query}\n```\nProceed?",
                schema=ConfirmAction,
            )
            if resp.action != "accept" or not resp.data or not resp.data.confirm:
                logger.warning("[delete_sql] User cancelled DELETE")
                return _error("DELETE cancelled.", status="cancelled")

        try:
            result = await execute(query, db)
            logger.info("[delete_sql] ✔ Executed: %s", query[:100])
            return _format_result(result, query)
        except Exception as e:
            logger.error("[delete_sql] ✗ Failed: %s", e)
            return _error(str(e), query=query)

    # -----------------------------------------------------------------------
    # INSERT — DML
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def insert_sql(
        ctx: Context,
        query: str,
        database: str | None = None,
    ) -> str:
        """
        Insert one or more rows. ONLY accepts INSERT statements.

        Args:
            query: Full INSERT statement.
                   Examples:
                       "INSERT INTO users (name, age) VALUES ('Alice', 30)"
                       "INSERT INTO logs (msg) VALUES ('a'), ('b'), ('c')"
            database: Target database. Omit to use the configured default.
        """
        db = database or default_database
        logger.debug("[insert_sql] db=%s query=%s", db, query)

        if not _starts_with(query, "INSERT"):
            return _error("Only INSERT statements are allowed with this tool.")
        if err := check_injection(query):
            return _error(err)

        try:
            result = await execute(query, db)
            logger.info("[insert_sql] ✔ Inserted rows: %d", result.row_count)
            return _format_result(result, query)
        except Exception as e:
            logger.error("[insert_sql] ✗ Failed: %s", e)
            return _error(str(e), query=query)

    # -----------------------------------------------------------------------
    # SELECT — read
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def select_query(
        ctx: Context,
        query: str,
        database: str | None = None,
        auto_limit: bool = True,
    ) -> str:
        """
        Execute a read-only SELECT statement and return matching rows.

        Use this for any SELECT — including JOINs, aggregations, subqueries.
        For exploring schema use `describe_table`. For optimizer plans use `explain_query`.

        Adds `LIMIT 10` automatically when the query has no LIMIT (set auto_limit=False to disable).

        Args:
            query: Full SELECT statement.
                   Example: "SELECT id, name FROM users WHERE age > 18 ORDER BY name"
            database: Target database. Omit to use the configured default.
            auto_limit: When True (default), append `LIMIT 10` if absent.
        """
        db = database or default_database
        logger.debug("[select_query] db=%s query=%s", db, query)

        if not _starts_with(query, "SELECT", "WITH"):
            return _error("Only SELECT (or WITH … SELECT) statements are allowed with this tool.")
        if err := check_injection(query):
            return _error(err)

        final_query = query
        if auto_limit and "LIMIT" not in query.upper():
            final_query = f"{query.rstrip().rstrip(';')} LIMIT 10"

        try:
            result = await execute(final_query, db)
            return json.dumps({
                "status": "success",
                "query": final_query,
                "data": result.rows,
                "row_count": len(result.rows),
                "execution_time_ms": result.execution_time_ms,
            }, indent=2, default=str)
        except Exception as e:
            logger.error("[select_query] ✗ Failed: %s", e)
            return _error(str(e), query=final_query)

    # -----------------------------------------------------------------------
    # DESCRIBE — metadata
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def describe_table(
        ctx: Context,
        table_name: str,
        database: str | None = None,
    ) -> str:
        """
        Inspect a table's structure — column names, types, nullability, keys, defaults.

        Use this when the user asks about table schema / columns / "what fields does X have".

        Args:
            table_name: Table to describe.
            database: Target database. Omit to use the configured default.
        """
        db = database or default_database
        logger.debug("[describe_table] table=%s db=%s", table_name, db)

        if err := validate_identifier(table_name):
            return _error(err)

        # MySQL uses DESCRIBE; asyncpg/PG also accepts this for compatibility via info_schema fallback.
        # Both engines support a simple DESCRIBE in pymysql / SELECT in asyncpg. We pick a portable form.
        query = (
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            f"WHERE table_name = '{table_name}' "
            "ORDER BY ordinal_position"
        )

        try:
            result = await execute(query, db)
            return json.dumps({
                "status": "success",
                "database": db,
                "table": table_name,
                "columns": result.rows,
                "column_count": len(result.rows),
                "execution_time_ms": result.execution_time_ms,
            }, indent=2, default=str)
        except Exception as e:
            logger.error("[describe_table] ✗ Failed: %s", e)
            return _error(str(e), query=query)

    # -----------------------------------------------------------------------
    # EXPLAIN — optimizer plan
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def explain_query(
        ctx: Context,
        query: str,
        database: str | None = None,
    ) -> str:
        """
        Show the query optimizer's plan WITHOUT executing the query.

        Use this to understand how the database will run a query — index usage,
        join order, estimated row counts.

        Args:
            query: SQL statement to explain (typically SELECT, but UPDATE/DELETE/INSERT also work).
                   Example: "SELECT * FROM users WHERE email = 'a@b.com'"
            database: Target database. Omit to use the configured default.
        """
        db = database or default_database
        logger.debug("[explain_query] db=%s query=%s", db, query)

        if err := check_injection(query):
            return _error(err)

        final_query = f"EXPLAIN {query}"
        try:
            result = await execute(final_query, db)
            return json.dumps({
                "status": "success",
                "query": final_query,
                "plan": result.rows,
                "execution_time_ms": result.execution_time_ms,
            }, indent=2, default=str)
        except Exception as e:
            logger.error("[explain_query] ✗ Failed: %s", e)
            return _error(str(e), query=final_query)

    # -----------------------------------------------------------------------
    # EXPLAIN ANALYZE — real execution timing
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def explain_analyze(
        ctx: Context,
        query: str,
        database: str | None = None,
    ) -> str:
        """
        Run EXPLAIN ANALYZE — actually executes the query and reports real timings per step.

        IMPORTANT: this RUNS the query. Use only on SELECT for safety; ANALYZE
        on INSERT/UPDATE/DELETE will mutate data.

        Args:
            query: SQL statement to analyze (SELECT recommended).
                   Example: "SELECT COUNT(*) FROM orders WHERE created_at > '2025-01-01'"
            database: Target database. Omit to use the configured default.
        """
        db = database or default_database
        logger.debug("[explain_analyze] db=%s query=%s", db, query)

        if not _starts_with(query, "SELECT", "WITH"):
            return _error("Only SELECT queries are accepted by explain_analyze for safety. Use explain_query for write statements.")
        if err := check_injection(query):
            return _error(err)

        final_query = f"EXPLAIN ANALYZE {query}"
        try:
            result = await execute(final_query, db)
            return json.dumps({
                "status": "success",
                "query": final_query,
                "plan": result.rows,
                "execution_time_ms": result.execution_time_ms,
            }, indent=2, default=str)
        except Exception as e:
            logger.error("[explain_analyze] ✗ Failed: %s", e)
            return _error(str(e), query=final_query)
