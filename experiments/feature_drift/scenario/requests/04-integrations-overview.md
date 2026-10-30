# Request 4 — What is connected?

I'm already losing track of what's connected and whether it works. Give me one place to look.

## What I need

`GET /api/integrations` → `200 {"integrations": [...]}` sorted by name, one entry per
integration Switchboard supports (today: `crm`, `docs`, `weatherco`):

```json
{"name": "crm", "kind": "rest", "status": "ok", "detail": null}
```

- `kind`: `rest` or `mcp`.
- `status`: `unconfigured` (no credentials; do not call the provider), `ok` (a cheap live call
  just succeeded), or `error` (the live call failed; put a short human reason in `detail`).

`GET /api/integrations/{name}` → the single entry; unknown name → `404 not_found`.

Every integration we add later must show up here too. For providers without a status endpoint,
any cheap read is a fine live check; a `404` for an unknown item still proves the credentials work.
