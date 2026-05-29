"""MCP Resources — read-only operations (SELECT, DESCRIBE, SHOW TABLES/DATABASES)."""

import json
import logging
from typing import Callable, Awaitable
from urllib.parse import unquote

from db import QueryResult, check_injection, validate_identifier

logger = logging.getLogger("db-mcp-server")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ExecuteFn = Callable[[str, str | None], Awaitable[QueryResult]]


def _error(message: str, **extra) -> str:
    return json.dumps({"status": "error", "message": message, **extra}, indent=2, default=str)


# ---------------------------------------------------------------------------
# MySQL resources
# ---------------------------------------------------------------------------


def register_mysql_resources(mcp, execute: ExecuteFn, default_database: str) -> None:
    """Register MySQL-specific read resources."""

    @mcp.resource("db://mysql/{database}/tables")
    async def mysql_show_tables(database: str | None = None) -> str:
        """List all tables in a MySQL database. Pass 'default' or omit to use configured default."""
        db = database if database and database != "default" else default_database
        logger.debug("[resource] mysql_show_tables db=%s", db)
        try:
            result = await execute("SHOW TABLES", db)
            tables = [list(row.values())[0] for row in result.rows]
            return json.dumps({
                "database": db,
                "tables": tables,
                "table_count": len(tables),
                "execution_time_ms": result.execution_time_ms,
            }, indent=2, default=str)
        except Exception as e:
            logger.error("[resource] mysql_show_tables failed: %s", e)
            return _error(str(e))

    @mcp.resource("db://mysql/{database}/tables/{table_name}/describe")
    async def mysql_describe_table(table_name: str, database: str | None = None) -> str:
        """Describe a MySQL table's columns, types, and keys. Pass 'default' or omit database to use configured default."""
        db = database if database and database != "default" else default_database
        table_name = unquote(table_name)
        if err := validate_identifier(table_name):
            return _error(err)
        logger.debug("[resource] mysql_describe_table table=%s db=%s", table_name, db)
        try:
            result = await execute(f"DESCRIBE {table_name}", db)
            return json.dumps({
                "database": db,
                "table": table_name,
                "columns": result.rows,
                "execution_time_ms": result.execution_time_ms,
            }, indent=2, default=str)
        except Exception as e:
            logger.error("[resource] mysql_describe_table failed: %s", e)
            return _error(str(e))

    @mcp.resource("db://mysql/databases")
    async def mysql_show_databases() -> str:
        """List all databases on the MySQL server."""
        logger.debug("[resource] mysql_show_databases")
        try:
            result = await execute("SHOW DATABASES", None)
            databases = [list(row.values())[0] for row in result.rows]
            return json.dumps({
                "databases": databases,
                "count": len(databases),
                "execution_time_ms": result.execution_time_ms,
            }, indent=2, default=str)
        except Exception as e:
            logger.error("[resource] mysql_show_databases failed: %s", e)
            return _error(str(e))

    @mcp.resource("db://mysql/{database}/query/{select_query}")
    async def mysql_select(select_query: str, database: str | None = None) -> str:
        """Run a SELECT query against MySQL. Adds LIMIT 10 if not specified. Pass 'default' or omit database to use configured default."""
        db = database if database and database != "default" else default_database
        select_query = unquote(select_query)
        logger.debug("[resource] mysql_select db=%s query=%s", db, select_query)

        if not select_query.strip().upper().startswith("SELECT"):
            return _error("Only SELECT queries allowed.")
        if err := check_injection(select_query):
            return _error(err)
        if "LIMIT" not in select_query.upper():
            select_query = f"{select_query.rstrip().rstrip(';')} LIMIT 10"

        try:
            result = await execute(select_query, db)
            return json.dumps({
                "status": "success",
                "query": select_query,
                "data": result.rows,
                "row_count": len(result.rows),
                "execution_time_ms": result.execution_time_ms,
            }, indent=2, default=str)
        except Exception as e:
            logger.error("[resource] mysql_select failed: %s", e)
            return _error(str(e), query=select_query)

    @mcp.resource("db://mysql/{database}/explain-analyze/{query_str}")
    async def mysql_explain_analyze(query_str: str, database: str | None = None) -> str:
        """Run EXPLAIN ANALYZE on a SELECT query against MySQL. Returns the real execution plan with actual timings. Pass 'default' or omit database to use configured default."""
        db = database if database and database != "default" else default_database
        query_str = unquote(query_str)
        logger.debug("[resource] mysql_explain_analyze db=%s query=%s", db, query_str)

        if not query_str.strip().upper().startswith("SELECT") and not query_str.strip().upper().startswith("WITH"):
            return _error("Only SELECT queries are allowed for EXPLAIN ANALYZE.")
        if err := check_injection(query_str):
            return _error(err)

        final_query = f"EXPLAIN ANALYZE {query_str}"
        try:
            result = await execute(final_query, db)
            return json.dumps({
                "status": "success",
                "query": final_query,
                "plan": result.rows,
                "execution_time_ms": result.execution_time_ms,
            }, indent=2, default=str)
        except Exception as e:
            logger.error("[resource] mysql_explain_analyze failed: %s", e)
            return _error(str(e), query=final_query)

# ---------------------------------------------------------------------------
# PostgreSQL resources
# ---------------------------------------------------------------------------


def register_pg_resources(mcp, execute: ExecuteFn, default_database: str) -> None:
    """Register PostgreSQL-specific read resources."""

    @mcp.resource("db://postgresql/{database}/tables")
    async def pg_show_tables(database: str | None = None) -> str:
        """List all tables in a PostgreSQL database (public schema). Pass 'default' or omit database to use configured default."""
        db = database if database and database != "default" else default_database
        logger.debug("[resource] pg_show_tables db=%s", db)
        query = (
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        )
        try:
            result = await execute(query, db)
            tables = [row["table_name"] for row in result.rows]
            return json.dumps({
                "database": db,
                "tables": tables,
                "table_count": len(tables),
                "execution_time_ms": result.execution_time_ms,
            }, indent=2, default=str)
        except Exception as e:
            logger.error("[resource] pg_show_tables failed: %s", e)
            return _error(str(e))

    @mcp.resource("db://postgresql/{database}/tables/{table_name}/describe")
    async def pg_describe_table(table_name: str, database: str | None = None) -> str:
        """Describe a PostgreSQL table's columns, types, and constraints. Pass 'default' or omit database to use configured default."""
        db = database if database and database != "default" else default_database
        table_name = unquote(table_name)
        if err := validate_identifier(table_name):
            return _error(err)
        logger.debug("[resource] pg_describe_table table=%s db=%s", table_name, db)
        query = (
            "SELECT column_name, data_type, is_nullable, column_default, character_maximum_length "
            "FROM information_schema.columns "
            f"WHERE table_schema = 'public' AND table_name = '{table_name}' "
            "ORDER BY ordinal_position"
        )
        try:
            result = await execute(query, db)
            return json.dumps({
                "database": db,
                "table": table_name,
                "columns": result.rows,
                "execution_time_ms": result.execution_time_ms,
            }, indent=2, default=str)
        except Exception as e:
            logger.error("[resource] pg_describe_table failed: %s", e)
            return _error(str(e))

    @mcp.resource("db://postgresql/databases")
    async def pg_show_databases() -> str:
        """List all databases on the PostgreSQL server."""
        logger.debug("[resource] pg_show_databases")
        query = "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname"
        try:
            result = await execute(query, None)
            databases = [row["datname"] for row in result.rows]
            return json.dumps({
                "databases": databases,
                "count": len(databases),
                "execution_time_ms": result.execution_time_ms,
            }, indent=2, default=str)
        except Exception as e:
            logger.error("[resource] pg_show_databases failed: %s", e)
            return _error(str(e))

    @mcp.resource("db://postgresql/{database}/query/{select_query}")
    async def pg_select(select_query: str, database: str | None = None) -> str:
        """Run a SELECT query against PostgreSQL. Adds LIMIT 10 if not specified. Pass 'default' or omit database to use configured default."""
        db = database if database and database != "default" else default_database
        select_query = unquote(select_query)
        logger.debug("[resource] pg_select db=%s query=%s", db, select_query)

        if not select_query.strip().upper().startswith("SELECT"):
            return _error("Only SELECT queries allowed.")
        if err := check_injection(select_query):
            return _error(err)
        if "LIMIT" not in select_query.upper():
            select_query = f"{select_query.rstrip().rstrip(';')} LIMIT 10"

        try:
            result = await execute(select_query, db)
            return json.dumps({
                "status": "success",
                "query": select_query,
                "data": result.rows,
                "row_count": len(result.rows),
                "execution_time_ms": result.execution_time_ms,
            }, indent=2, default=str)
        except Exception as e:
            logger.error("[resource] pg_select failed: %s", e)
            return _error(str(e), query=select_query)

    @mcp.resource("db://postgresql/{database}/explain-analyze/{query_str}")
    async def pg_explain_analyze(query_str: str, database: str | None = None) -> str:
        """Run EXPLAIN ANALYZE on a SELECT query against PostgreSQL. Returns the real execution plan with actual timings. Pass 'default' or omit database to use configured default."""
        db = database if database and database != "default" else default_database
        query_str = unquote(query_str)
        logger.debug("[resource] pg_explain_analyze db=%s query=%s", db, query_str)

        if not query_str.strip().upper().startswith("SELECT") and not query_str.strip().upper().startswith("WITH"):
            return _error("Only SELECT queries are allowed for EXPLAIN ANALYZE.")
        if err := check_injection(query_str):
            return _error(err)

        final_query = f"EXPLAIN ANALYZE {query_str}"
        try:
            result = await execute(final_query, db)
            return json.dumps({
                "status": "success",
                "query": final_query,
                "plan": result.rows,
                "execution_time_ms": result.execution_time_ms,
            }, indent=2, default=str)
        except Exception as e:
            logger.error("[resource] pg_explain_analyze failed: %s", e)
            return _error(str(e), query=final_query)