# Request 19 — Unified CLI, MCP, and HTTP Telemetry & Simulation Control Plane

Expose field stepping, dialect inspection, quarantine status, and conservation audit reports across CLI, MCP, and HTTP.

Our operators script against fields from shell jobs, and our AI assistant wants the same
operations as MCP tools. Whichever door they use, they must see and change the same fields,
and get exactly the same answers as the HTTP API gives.

## What I need

### The operations

| operation | CLI | MCP tool | HTTP equivalent (unchanged) |
| --- | --- | --- | --- |
| field summary | `field --field ID` | `get_field {"field"}` | `GET /api/fields/{id}` |
| step | `step --field ID [--ticks N \| --sub-ticks N]` | `step_field {"field", "ticks"?, "sub_ticks"?}` | `POST /api/fields/{id}/step` |
| dialects | `dialects` | `list_dialects {}` | `GET /api/dialects` |
| quarantine status | `quarantine --field ID` | `quarantine_status {"field"}` | `GET /api/fields/{id}/quarantine` |
| conservation audit | `audit --field ID` | `audit_field {"field"}` | `GET /api/fields/{id}/audit` |

Results are the same JSON documents the HTTP endpoints return, and validation and error codes
are the same (unknown field → `not_found`, bad tick counts → `invalid_request`, …).

### Shared state

The CLI and the MCP server are separate processes started from the repository root with the
same `AETHERLOOM_DATA_DIR` as the service. They read the fields stored there and save any
change (stepping) back, so the service sees it when it starts, and they see everything the
service saved. Only one process uses a data directory at a time (the service is stopped while
the CLI or MCP server runs).

### CLI

`.venv/bin/python -m aetherloom cli <operation> [options]` prints the result as one line of JSON
on stdout and exits `0`. On error it prints the error envelope
`{"error": {"code": "...", "message": "..."}}` on stdout and exits `1`.

### MCP server

`.venv/bin/python -m aetherloom mcp` speaks JSON-RPC 2.0 over stdio: one JSON message per line
on stdin, one response per line on stdout, in order; it exits at end of input. Messages
without an `id` are notifications and get no response.

- `initialize` → `{"protocolVersion": "2025-06-18", "serverInfo": {"name": "aetherloom", "version": "..."}, "capabilities": {"tools": {}}}`.
- `tools/list` → `{"tools": [{"name", "description", "inputSchema"}, ...]}` with the five tools
  above (`inputSchema` is a JSON Schema object).
- `tools/call` with `{"name": "step_field", "arguments": {"field": "f1", "ticks": 2}}` →
  `{"content": [{"type": "text", "text": "<the result as JSON>"}], "structuredContent": <the result>, "isError": false}`.
  If the operation fails: `"isError": true`, and both `structuredContent` and the text hold
  the error envelope.
- Unknown method → JSON-RPC error `-32601`; unknown tool → `-32602`; a line that is not JSON →
  `-32700` with `"id": null`.
