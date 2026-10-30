# Request 9 — Let the assistant use everything

Our AI assistant needs to discover and use everything Switchboard can do. I don't want us
writing assistant glue for every feature, now or later: give it a catalogue and one way to
invoke anything in it.

## What I need

`GET /api/capabilities` →

```json
{"capabilities": [{"name": "crm.search_contacts", "integration": "crm", "description": "...",
                   "input_schema": {"type": "object", "properties": {"search": {"type": "string"}, "limit": {"type": "integer"}}, "required": []},
                   "available": true}]}
```

Sorted by name. `available` is false while the integration has no credentials.
`input_schema` is JSON Schema; each property's `type` is a single type name such as `"string"`. At least these, with these inputs (`*` = required):

| Capability | Input | Output: same JSON as |
| --- | --- | --- |
| `weatherco.forecast` | `city`* string, `days` integer | `GET /api/weather` |
| `crm.search_contacts` | `search` string, `limit` integer | `GET /api/contacts` |
| `crm.create_contact` | `name`* string, `email`* string, `company` string | `POST /api/contacts` |
| `docs.search` | `q`* string, `limit` integer | `GET /api/docs/search` |
| `docs.get` | `id`* string | `GET /api/docs/{id}` |
| `calendar.list_events` | `from`* string, `to`* string | `GET /api/calendar/events` |
| `ledger.list_invoices` | `status` string | `GET /api/invoices` |
| `tickets.create` | `title`* string, `body`* string, `priority` string | `POST /api/tickets` |
| `tickets.list` | `status` string | `GET /api/tickets` |

`POST /api/capabilities/{name}/invoke` with `{"input": {...}}` → `200 {"output": <that JSON>}`.

- Unknown capability → `404 not_found`. Input violating the schema, or a body without an
  `input` object → `422 invalid_request`.
- Otherwise exactly the same errors as the matching endpoint (`409 not_configured`, `404 unknown_city`, ...).
- Every future integration must expose its operations here too.
