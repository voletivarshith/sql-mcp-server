# sql-mcp-server

An MCP (Model Context Protocol) server that lets AI agents execute MySQL and PostgreSQL queries on your behalf. Connect your database, point your agent at this server, and interact with your data using natural language.

## Why?

Writing SQL by hand is slow. Debugging queries is tedious. This server gives AI agents (Claude, Copilot, Cursor, etc.) direct, safe access to your database

## Features

- **Dual engine** — MySQL (`pymysql`) and PostgreSQL (`asyncpg`), selected via one env var
- **9 categorized tools** — each tool accepts only its designated SQL type (no generic "run anything")
- **Elicitation** — destructive operations require explicit user confirmation before execution
- **SQL injection protection** — regex-based pattern detection blocks multi-statement, UNION, and comment injection
- **Auto LIMIT** — SELECT queries without LIMIT get `LIMIT 10` appended automatically
- **EXPLAIN & EXPLAIN ANALYZE** — built-in query profiling tools
- **Startup connectivity check** — server verifies it can reach your DB before accepting connections
- **Agent Skill file** — bundled `SKILL.md` teaches agents exactly when and how to use each tool

## Project Structure

```
sql-mcp-server/
├── src/
│   ├── server.py        # Entry point: env validation, engine selection, server startup
│   ├── db.py            # Connection layer: pymysql (threaded) + asyncpg (native async)
│   ├── tools.py         # MCP tools: categorized write/DDL/read operations
│   └── resources.py     # MCP resources: read-only URI-based operations
├── sql-mcp-server/
│   └── SKILL.md         # Agent Skill — copy into your project for better tool selection
├── .env.example         # Template for environment variables
├── pyproject.toml       # Dependencies and project metadata
├── LICENSE              # MIT license
└── README.md
```

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager
- A running MySQL or PostgreSQL server

## Setup

```bash
# Clone and install
cd sql-mcp-server
uv sync
```

Create a `.env` file:

```env
# Required: choose your engine
DB_ENGINE=mysql          # or "pgsql"

# MySQL (when DB_ENGINE=mysql)
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=  
MYSQL_DATABASE=mydb

# PostgreSQL (when DB_ENGINE=pgsql)
PG_HOST=localhost
PG_PORT=5432
PG_USER=postgres
PG_PASSWORD=
PG_DATABASE=mydb
```

## Running the Server

```bash
# Default: streamable-http transport
uv run python src/server.py
```

On successful startup you'll see:

```
✔ Connected to MySQL successfully (host=localhost, db=mydb)
```

If credentials are wrong or the DB is unreachable, the server exits immediately with a clear error.

## Using the Agent Skill (SKILL.md)

The `sql-mcp-server/SKILL.md` file follows the [Agent Skills](https://agentskills.io) open standard. It teaches AI agents **when** and **how** to invoke each tool correctly.

### How to use it

** Copy into your project root:**

```bash
cp sql-mcp-server/SKILL.md /path/to/your/project/sql-mcp-server/SKILL.md
```

Most MCP-compatible agents (Claude Code, Cursor, Copilot, Roo Code, Amp, etc.) will auto-discover skill files in your workspace and use them to improve tool selection.

## Tools

| Tool | Accepts | Description |
|------|---------|-------------|
| `select_query` | SELECT, WITH | Query data with auto-LIMIT |
| `describe_table` | table name | Column metadata via information_schema |
| `explain_query` | any SQL | Optimizer plan (does NOT execute) |
| `explain_analyze` | SELECT only | Real execution plan with timings |
| `create_table` | DDL | Create a new table |
| `alter_sql` | ALTER TABLE | Modify table structure |
| `insert_sql` | INSERT | Add rows |
| `update_sql` | UPDATE | Modify rows (full query or parts) |
| `delete_sql` | DELETE / TRUNCATE / DROP TABLE | Remove data (always confirms) |

## Resources (URI-based, read-only)

| URI | Purpose |
|-----|---------|
| `db://{engine}/{database}/tables` | List all tables |
| `db://{engine}/{database}/tables/{name}/describe` | Describe a table |
| `db://{engine}/databases` | List all databases |
| `db://{engine}/{database}/query/{encoded_sql}` | Run a SELECT |
| `db://{engine}/{database}/explain-analyze/{encoded_sql}` | EXPLAIN ANALYZE a SELECT |

Pass `default` as `{database}` to use the configured default from `.env`.

## Safety

- **Elicitation prompts** — DROP, TRUNCATE, DELETE, and UPDATE operations ask the user to confirm
- **SQL injection detection** — multi-statement, UNION injection, and comment patterns are blocked
- **Identifier validation** — table/column names must match `^[a-zA-Z_][a-zA-Z0-9_.]*$`
- **Statement isolation** — each tool rejects SQL it wasn't designed for
- **No credential exposure** — passwords are never logged or returned in responses

## MCP Client Configuration

Add to your MCP client config (e.g., Claude Desktop `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "sql-mcp-server": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/sql-mcp-server", "python", "src/server.py"],
      "env": {
        "DB_ENGINE": "mysql",
        "MYSQL_USER": "root",
        "MYSQL_PASSWORD": "",
        "MYSQL_DATABASE": "mydb"
      }
    }
  }
}
```

## License

MIT
