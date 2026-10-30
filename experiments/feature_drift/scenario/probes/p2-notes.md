# Request — Notes

Add our Notes MCP server as an integration. It must behave like every other integration
Switchboard has (credentials, overview and health, capabilities, workspaces, audit, quotas at
1 unit, switching off and credential removal, circuit breaking and timeouts). Do not cache.

`PUT /api/credentials/notes` `{"token": "..."}`. Overview kind `mcp`.

- Capability `notes.create`, input `title`* string, `text`* string → `{"id": ..., "title": ..., "text": ...}`.
- Capability `notes.search`, input `q`* string → `{"notes": [{"id", "title", "text"}]}`.

Server: `POST {EXTERNAL}/mcp/notes`, the same (current) MCP protocol as our other servers.
Tools `create_note` `{"title", "text"}` → `{"note": {...}}` and `search_notes` `{"query"}` → `{"notes": [...]}`.
