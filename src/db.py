"""Database connection layer using pymysql (MySQL) and asyncpg (PostgreSQL)."""

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import asyncpg
import pymysql
import pymysql.cursors
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("db-mcp-server")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def get_mysql_config() -> dict[str, Any]:
    """Build MySQL connection kwargs from environment variables."""
    return {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "database": os.getenv("MYSQL_DATABASE", ""),
    }


def get_pg_config() -> dict[str, Any]:
    """Build PostgreSQL connection kwargs from environment variables."""
    return {
        "host": os.getenv("PG_HOST", "localhost"),
        "port": int(os.getenv("PG_PORT", "5432")),
        "user": os.getenv("PG_USER", "postgres"),
        "password": os.getenv("PG_PASSWORD", ""),
        "database": os.getenv("PG_DATABASE", ""),
    }


# ---------------------------------------------------------------------------
# Query result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QueryResult:
    rows: list[dict[str, Any]]
    row_count: int
    execution_time_ms: float


# ---------------------------------------------------------------------------
# SQL injection guard
# ---------------------------------------------------------------------------

_INJECTION_PATTERN = re.compile(
    r";\s*(DROP|ALTER|DELETE|INSERT|UPDATE|CREATE|TRUNCATE|EXEC|EXECUTE)\b"
    r"|--\s*$"
    r"|/\*.*?\*/"
    r"|\bUNION\s+(ALL\s+)?SELECT\b",
    re.IGNORECASE | re.DOTALL,
)

_VALID_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.`\"]*$")


def check_injection(query: str) -> str | None:
    """Return error message if query contains injection patterns, else None."""
    if _INJECTION_PATTERN.search(query):
        return "Query rejected: potential SQL injection detected."
    if query.count(";") > 1:
        return "Query rejected: multiple statements not allowed."
    return None


def validate_identifier(name: str) -> str | None:
    """Return error if identifier is invalid, else None."""
    if not _VALID_IDENTIFIER.match(name):
        return f"Invalid identifier: '{name}'. Only letters, digits, underscores, and dots allowed."
    return None


# ---------------------------------------------------------------------------
# MySQL execution (pymysql is synchronous — run in thread pool)
# ---------------------------------------------------------------------------


def _run_mysql_query(query: str, database: str | None = None) -> QueryResult:
    """Synchronous MySQL query execution via pymysql."""
    config = get_mysql_config()
    if database:
        config["database"] = database

    logger.debug("[MySQL] host=%s port=%s db=%s", config["host"], config["port"], config["database"])
    logger.debug("[MySQL] query=%s", query)

    start = time.perf_counter()
    conn = pymysql.connect(**config, cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall() if cur.description else []
            conn.commit()
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.debug("[MySQL] %.2fms | rows_affected=%d", elapsed_ms, cur.rowcount)
            return QueryResult(
                rows=list(rows),
                row_count=cur.rowcount,
                execution_time_ms=round(elapsed_ms, 2),
            )
    finally:
        conn.close()


async def execute_mysql(query: str, database: str | None = None) -> QueryResult:
    """Async wrapper — runs pymysql in a thread pool to avoid blocking."""
    return await asyncio.to_thread(_run_mysql_query, query, database)


def ping_mysql() -> None:
    """Verify MySQL connectivity by opening a connection and pinging."""
    config = get_mysql_config()
    conn = pymysql.connect(**config)
    try:
        conn.ping(reconnect=False)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PostgreSQL execution (asyncpg is natively async)
# ---------------------------------------------------------------------------


async def execute_pg(query: str, database: str | None = None) -> QueryResult:
    """Execute a query against PostgreSQL using asyncpg."""
    config = get_pg_config()
    if database:
        config["database"] = database

    logger.debug("[PG] host=%s port=%s db=%s", config["host"], config["port"], config["database"])
    logger.debug("[PG] query=%s", query)

    start = time.perf_counter()
    conn = await asyncpg.connect(**config)
    try:
        stmt = await conn.prepare(query)
        if stmt.get_attributes():
            records = await conn.fetch(query)
            rows = [dict(r) for r in records]
            row_count = len(rows)
        else:
            status = await conn.execute(query)
            rows = []
            parts = status.split()
            row_count = int(parts[-1]) if parts[-1].isdigit() else 0

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.debug("[PG] %.2fms | row_count=%d", elapsed_ms, row_count)
        return QueryResult(
            rows=rows,
            row_count=row_count,
            execution_time_ms=round(elapsed_ms, 2),
        )
    finally:
        await conn.close()


async def ping_pg() -> None:
    """Verify PostgreSQL connectivity by opening and closing a connection."""
    config = get_pg_config()
    conn = await asyncpg.connect(**config)
    await conn.close()
