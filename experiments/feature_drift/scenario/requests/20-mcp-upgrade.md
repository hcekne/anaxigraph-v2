# Request 20 — MCP servers are upgrading

Heads up: all our MCP servers (docs, tickets, lingo) move to the new protocol revision this week.
Nothing user-facing changes, and every existing feature must keep working, with the same results.

## Protocol changes

1. `initialize` must send `"protocolVersion": "2026-03-26"`. Servers reject other versions with a
   JSON-RPC error (HTTP `400`).
2. The `initialize` HTTP response carries an `Mcp-Session-Id` header. Every later request must
   send it back as `Mcp-Session-Id`. Missing it → HTTP `400`.
3. Sessions can expire at any time: HTTP `404` with JSON-RPC error code `-32001`. Then initialize
   a new session and retry the call once.
4. `tools/call` results now carry the data in `structuredContent` (an object). The text content
   is no longer JSON. Failures still set `"isError": true`, with `structuredContent: {"error": "<reason>"}`.

The old protocol is switched off when the upgrade lands, so support only the new one.
