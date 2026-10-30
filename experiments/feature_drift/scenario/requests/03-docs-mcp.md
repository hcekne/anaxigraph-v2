# Request 3 — Search our engineering docs

Our engineering docs sit behind an internal MCP server (the same one the AI assistant uses).
People want to search them from Switchboard too.

## What I need

`PUT /api/credentials/docs` with `{"token": "<non-empty>"}` → `204`.

`GET /api/docs/search?q=deploy&limit=5` — `q` required, `limit` 1–20 default 5 →
`200 {"results": [{"id": "doc-1", "title": "...", "snippet": "...", "url": "..."}]}` in server order.

`GET /api/docs/{id}` → `200 {"id": ..., "title": ..., "body": ..., "url": ...}`; unknown id → `404 not_found`.

## Docs MCP server

Endpoint: `POST {EXTERNAL}/mcp/docs`, JSON-RPC 2.0 over HTTP, header `Authorization: Bearer <token>`
(bad token → HTTP `401`). Each request body is one JSON-RPC message; the response body is the reply.

1. Start with `initialize`: `{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "switchboard", "version": "0.1"}}}`.
   The result echoes `protocolVersion`, `serverInfo` and `capabilities`.
2. `tools/list` lists tools with JSON Schemas.
3. `tools/call` with `{"name": "search_docs", "arguments": {"query": "deploy", "limit": 5}}` or
   `{"name": "get_doc", "arguments": {"id": "doc-1"}}`. The result is
   `{"content": [{"type": "text", "text": "<JSON document>"}], "isError": false}`: parse the text.
   `search_docs` returns `{"results": [...]}` in the shape above; `get_doc` returns the document.
   Failures come back with `"isError": true` and text `Error: <reason>`; an unknown doc gives `Error: not_found`.
