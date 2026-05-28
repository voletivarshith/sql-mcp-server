"""MCP Server entry point — configures DB engine, validates env, registers tools/resources."""

import logging
import os
import sys

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

# ---------------------------------------------------------------------------
# Logging — DEBUG level for full visibility
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("db-mcp-server")

# ---------------------------------------------------------------------------
# Determine database engine from environment
# ---------------------------------------------------------------------------

DB_ENGINE = os.getenv("DB_ENGINE", "").strip().lower()

if DB_ENGINE not in ("mysql", "pgsql"):
    logger.critical(
        "DB_ENGINE env var must be 'mysql' or 'pgsql'. Got: '%s'",
        os.getenv("DB_ENGINE", ""),
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Validate required credentials
# ---------------------------------------------------------------------------

if DB_ENGINE == "mysql":
    _user = os.getenv("MYSQL_USER", "")
    _password = os.getenv("MYSQL_PASSWORD", "")
    _database = os.getenv("MYSQL_DATABASE", "")
    _prefix = "MYSQL"
else:
    _user = os.getenv("PG_USER", "")
    _password = os.getenv("PG_PASSWORD", "")
    _database = os.getenv("PG_DATABASE", "")
    _prefix = "PG"

_missing = []
if not _user:
    _missing.append(f"{_prefix}_USER")
if not _database:
    _missing.append(f"{_prefix}_DATABASE")

if _missing:
    logger.critical("Missing required env vars: %s", ", ".join(_missing))
    sys.exit(1)

logger.info("DB_ENGINE=%s | user=%s | database=%s", DB_ENGINE, _user, _database)

# ---------------------------------------------------------------------------
# Create the MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "sql-mcp-server",
    instructions=f"""Database MCP server running in **{DB_ENGINE}** mode.

TOOLS: write operations (CREATE, INSERT, UPDATE, DELETE, ALTER, DROP).
RESOURCES: read operations (SELECT, SHOW TABLES, DESCRIBE, SHOW DATABASES).

Destructive operations (DROP, TRUNCATE, DELETE, UPDATE) require user confirmation.
All functions accept an optional `database` parameter; defaults to '{_database}'.
""",
)

# ---------------------------------------------------------------------------
# Bind the correct execution function and register tools/resources
# ---------------------------------------------------------------------------

from tools import register_tools
from resources import register_mysql_resources, register_pg_resources

if DB_ENGINE == "mysql":
    from db import execute_mysql as _execute, ping_mysql

    register_tools(mcp, execute=_execute, default_database=_database)
    register_mysql_resources(mcp, execute=_execute, default_database=_database)
    logger.info("Registered MySQL tools and resources.")

    try:
        ping_mysql()
        logger.info("✔ Connected to MySQL successfully (host=%s, db=%s)", os.getenv("MYSQL_HOST", "localhost"), _database)
    except Exception as e:
        logger.critical("Failed to connect to MySQL: %s", e)
        sys.exit(1)
else:
    from db import execute_pg as _execute, ping_pg

    register_tools(mcp, execute=_execute, default_database=_database)
    register_pg_resources(mcp, execute=_execute, default_database=_database)
    logger.info("Registered PostgreSQL tools and resources.")

    import asyncio
    try:
        asyncio.run(ping_pg())
        logger.info("✔ Connected to PostgreSQL successfully (host=%s, db=%s)", os.getenv("PG_HOST", "localhost"), _database)
    except Exception as e:
        logger.critical("Failed to connect to PostgreSQL: %s", e)
        sys.exit(1)

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
