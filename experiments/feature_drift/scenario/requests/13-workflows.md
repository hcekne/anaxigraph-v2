# Request 13 — Chain things together

Ops keeps doing the same sequences by hand: "look up this contact, then raise a ticket
mentioning them". Let them save workflows made of capability steps and run them.

## What I need

`POST /api/workflows` →

```json
{"name": "Raise follow-up",
 "steps": [
   {"id": "find", "capability": "crm.search_contacts", "input": {"search": "{{params.name}}", "limit": 1}},
   {"id": "raise", "capability": "tickets.create",
    "input": {"title": "Call {{steps.find.output.contacts.0.name}}", "body": "{{steps.find.output.contacts.0.email}}", "priority": "normal"}}]}
```

→ `201` with the workflow plus its `id`. At least one step, unique step ids, known capabilities,
and templates may only reference `params` or **earlier** steps; otherwise `422 invalid_request`.
`GET /api/workflows` → `{"workflows": [{"id", "name"}]}`; `GET /api/workflows/{id}` → the workflow or `404 not_found`.

`POST /api/workflows/{id}/run` with `{"params": {...}}` → `200`:

```json
{"status": "succeeded", "steps": [{"id": "find", "status": "succeeded", "output": {...}}, ...]}
```

- Steps run in order through the capability layer (quotas, audit and so on all apply).
- A failing step gets `"status": "failed", "error": {"code": ..., "message": ...}` (the code the
  capability would return); later steps get `"status": "skipped"`; the run's status is `failed`.
- Templates: `{{params.<name>}}` and `{{steps.<id>.output.<path>}}`, where `<path>` is dot-separated
  and numeric parts index lists. Resolve templates anywhere inside `input` (nested objects and
  lists too). A string that is exactly one template takes the referenced value with its JSON
  type; otherwise the value is inserted as text. A missing value fails that step with `template_error`; a present `null` is not
  missing and renders as an empty string inside text.

Workflows belong to a workspace and survive restarts.
