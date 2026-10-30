# Request 12 — Usage limits and billing

We bill customers for bundles of external calls, and Northwind's contract caps them. I need
per-workspace monthly quotas per integration, and usage I can show on an invoice.

## What I need

Setting `quotas`: `{"crm": 100, "ledger": 10}` — integration name → monthly limit (integer ≥ 0).
Integrations not listed are unlimited. Default `{}`. Unknown integration names or
limits that are not non-negative integers → `422 invalid_request`.

- **Billing unit:** one per user-facing operation that reaches a provider (an endpoint call or a
  capability invocation), however many HTTP requests it needs (pages, retries, tokens). Ledger
  operations cost **2** units. Operations rejected before contacting the provider (validation,
  not configured) cost nothing. Health checks cost nothing.
- If an operation would take an integration over its quota, reject it with `429 quota_exceeded`
  **without** contacting the provider.

`GET /api/usage` (for the request's workspace) →

```json
{"period": "2026-10", "integrations": {"crm": {"used": 3, "quota": 100}, "docs": {"used": 0, "quota": null}}}
```

Every integration appears. `period` is the current UTC month; usage resets each month. Usage
survives restarts.
