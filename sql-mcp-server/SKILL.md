---
name: sql-mcp-server
description: >
  Execute SQL against MySQL or PostgreSQL databases via categorized MCP tools.
  Use when the user asks to query, describe, explain, create, alter, insert,
  update, delete, truncate, or drop tables. Provides read tools (select,
  describe, explain, explain-analyze), write tools (insert, update, alter,
  create-table), and destructive tools (delete, truncate, drop-table) with
  built-in injection protection and elicitation on destructive operations.
metadata:
  author: varshith
  version: "1.0"
---

# Instructions

## Overview

This MCP server provides **9 strictly categorized tools** for SQL operations.
Each tool accepts ONLY its designated statement type — calling the wrong tool
will return an error. All tools share an optional `database` parameter that
defaults to the server's configured database when omitted.

## Step 1: Identify the operation type

Map the user's request to exactly one tool:

- **Read data** → `select_query`
- **Inspect schema** → `describe_table`
- **View query plan** → `explain_query`
- **Profile with real execution** → `explain_analyze`
- **Create a table** → `create_table`
- **Modify table structure** → `alter_sql`
- **Insert rows** → `insert_sql`
- **Update existing rows** → `update_sql`
- **Delete rows / truncate / drop table** → `delete_sql`

## Step 2: Construct tool arguments

### `select_query`
Accepts only `SELECT` or `WITH … SELECT`. Auto-appends `LIMIT 10` unless the
query already has one or `auto_limit=False`.

```json
{ "query": "SELECT id, name FROM users WHERE age > 18", "auto_limit": true }
```

### `describe_table`
Pass the table name. Returns columns, types, nullability, defaults.

```json
{ "table_name": "orders" }
```

### `explain_query`
Pass any SQL statement. Returns the optimizer plan WITHOUT executing.

```json
{ "query": "SELECT * FROM orders WHERE user_id = 5" }
```

### `explain_analyze`
Restricted to `SELECT`/`WITH` only (it executes the query). Returns real timing.

```json
{ "query": "SELECT COUNT(*) FROM orders WHERE created_at > '2025-01-01'" }
```

### `create_table`
Pass `table_name` and comma-separated `columns` DDL.

```json
{
  "table_name": "users",
  "columns": "id INT PRIMARY KEY AUTO_INCREMENT, name VARCHAR(255) NOT NULL, email VARCHAR(255)",
  "if_not_exists": true
}
```

### `alter_sql`
Pass a complete `ALTER TABLE …` statement. Elicits confirmation if it contains DROP.

```json
{ "query": "ALTER TABLE users ADD COLUMN phone VARCHAR(20)" }
```

### `insert_sql`
Pass a complete `INSERT INTO …` statement. No confirmation needed.

```json
{ "query": "INSERT INTO users (name, age) VALUES ('Alice', 30)" }
```

### `update_sql`
Two calling styles — pick whichever is clearer:

**Style A — full query:**
```json
{ "query": "UPDATE users SET status='active' WHERE id = 5" }
```

**Style B — parts (tool builds the query):**
```json
{ "table_name": "users", "set_clause": "status='active'", "where_clause": "id = 5" }
```

Omitting `where_clause` in Style B updates ALL rows (triggers strong confirmation).

### `delete_sql`
Accepts `DELETE FROM …`, `TRUNCATE TABLE …`, or `DROP TABLE …` only.
`DROP DATABASE/SCHEMA/INDEX` are blocked.

```json
{ "query": "DELETE FROM users WHERE id = 5" }
{ "query": "TRUNCATE TABLE sessions" }
{ "query": "DROP TABLE IF EXISTS old_logs" }
```

## Step 3: Handle confirmations

Destructive tools trigger elicitation prompts. Relay these to the user.

| Scenario | Confirmation type |
|---|---|
| `update_sql` with WHERE | Simple confirm |
| `update_sql` without WHERE | Type `DROP` to confirm |
| `delete_sql` DELETE with WHERE | Simple confirm |
| `delete_sql` DELETE without WHERE | Type `DROP` to confirm |
| `delete_sql` TRUNCATE or DROP | Type `DROP` to confirm |
| `alter_sql` containing DROP | Simple confirm |
| All other tools | No confirmation |

## Step 4: Interpret the response

All tools return JSON. Check the `status` field:

- `"success"` — operation completed. Key fields: `rows_affected`, `execution_time_ms`, `data`.
- `"error"` — something went wrong. Show `message` to user.
- `"cancelled"` — user declined. Do NOT retry.

## Resources (read-only, URI-based)

These are available for lightweight reads without calling tools:

| URI template | Purpose |
|---|---|
| `db://{engine}/{database}/tables` | List tables |
| `db://{engine}/{database}/tables/{table_name}/describe` | Describe columns |
| `db://{engine}/databases` | List all databases |
| `db://{engine}/{database}/query/{url_encoded_select}` | Run a SELECT (auto LIMIT 10) |
| `db://{engine}/{database}/explain-analyze/{url_encoded_select}` | Run EXPLAIN ANALYZE on a SELECT |

Where `{engine}` is `mysql` or `postgresql`. Pass `default` as `{database}` to
use the configured default. URI parameters must be URL-encoded.

The `explain-analyze` resource only accepts SELECT/WITH queries for safety — it
actually executes the query. Use it when the user wants real performance data
via resource URI rather than the `explain_analyze` tool.

## Common edge cases

- User says "show me the table" → use `describe_table` (schema), not `select_query`
- User says "show me the data" → use `select_query`
- User says "remove all data but keep the table" → `delete_sql` with `TRUNCATE TABLE`
- User says "delete the table entirely" → `delete_sql` with `DROP TABLE`
- User says "why is my query slow" → start with `explain_query`; only use
  `explain_analyze` if user explicitly asks for real measurements
- User gives raw SQL starting with SELECT → `select_query`
- User gives raw SQL starting with UPDATE → `update_sql` (pass as `query`)
- User gives raw SQL starting with INSERT → `insert_sql`

## Safety constraints

1. Never call `explain_analyze` on INSERT/UPDATE/DELETE — the tool rejects it.
2. Never call `delete_sql` when the intent is UPDATE.
3. If a tool returns `"cancelled"`, stop — the user explicitly declined.
4. If a tool returns `"error"`, surface the message verbatim and ask the user.
