# Request 19 — MCP & HTTP JSON-RPC Transport Parity for ChronoWeave

Expose all ChronoWeave inspection, branch simulation, and audit export operations identically over CLI, MCP stdio, and HTTP.

Our AI assistant and our ops scripts want the same operations the dashboards use. Same
inputs, same answers, same errors, whichever way you come in.

## What I need

### Operations

Every operation takes a JSON object of arguments; every one accepts an optional `tenant`
(default `"default"`, same as the `X-Tenant` header) and runs as `owner`. Its result is exactly
the JSON body of the HTTP equivalent, and its errors are the same error envelope with the same
code.

| operation | arguments | HTTP equivalent |
| --- | --- | --- |
| `get_assertion` | `id`*, `branch` (default `main`), `asserted_at` | `GET /api/branches/{branch}/assertions/{id}` |
| `list_assertions` | `branch`, `key`, `prefix`, `asserted_at`, `status` | `GET /api/branches/{branch}/assertions` |
| `materialize` | `branch`, `effective_at`, `asserted_at`, `prefix` | `GET /api/branches/{branch}/state` |
| `list_branches` | — | `GET /api/branches` |
| `diff_branch` | `branch`*, `effective_at` | `GET /api/branches/{branch}/diff` |
| `query` | `q`*, `branch` | `POST /api/query` |
| `simulate` | `from`, `assertions`*, `effective_at` | `POST /api/simulate` (new, below) |
| `export_bundle` | `effective_from`, `effective_to`, `asserted_at` | `GET /api/bundles/export` |
| `audit_graph` | `asserted_at` | `GET /api/audit/graph` |

(* required; `branch` defaults to `main` wherever it is optional.) For `export_bundle` the result is `{"bundle_base64": "<base64 of exactly the
bytes HTTP returns>", "digest": "<bundle digest>"}`. Missing or ill-typed arguments →
`invalid_request`.

**Simulation.** `POST /api/simulate` with
`{"from": {"effective_at": E, "asserted_at": A}, "assertions": [<assertion bodies>], "effective_at": X}`
(`from` and `effective_at` optional, as for forking and diffing) answers what
`POST /api/branches` + writing those assertions in order + `GET .../diff?effective_at=X` would
answer, with `"branch": null`, while storing nothing at all (no branch, no budget use, no
events). The first assertion that would be refused gives the same error the branch write
would.

### CLI

`.venv/bin/python -m chronoweave cli <operation> [--args '<JSON object>']`, run from the
repository root with the same environment as the service (it may run while the service is
running). It prints the result as one JSON document on stdout and exits `0`; on error it prints
the error envelope and exits `1`.

### MCP

`.venv/bin/python -m chronoweave mcp` speaks JSON-RPC 2.0 over stdin/stdout, one JSON message
per line:

- `initialize` → `{"protocolVersion": "2025-06-18", "serverInfo": {"name": "chronoweave", "version": "..."}, "capabilities": {"tools": {}}}`.
- `tools/list` → `{"tools": [{"name", "description", "inputSchema"}, ...]}`, one tool per
  operation, `inputSchema` a JSON Schema object.
- `tools/call` with `{"name", "arguments"}` →
  `{"content": [{"type": "text", "text": "<the result as JSON>"}], "structuredContent": <result>, "isError": false}`;
  for an operation error, `isError: true` and the error envelope as `structuredContent`
  (and as the text).
- Unknown method → JSON-RPC error `-32601`; unknown tool → JSON-RPC error `-32602`.
  Notifications (no `id`) get no response.
