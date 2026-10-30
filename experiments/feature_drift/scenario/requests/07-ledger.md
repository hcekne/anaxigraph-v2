# Request 7 — Open invoices

Finance wants open invoices in Switchboard. We use Ledgerly. Warning from finance: Ledgerly
rate-limits hard, and it bites at month end.

## What I need

`PUT /api/credentials/ledger` with `{"api_key": "...", "account_id": "..."}` → `204`.

`GET /api/invoices?status=open` — `status` is `open` (default), `paid` or `all`; else `422 invalid_request`.

```json
{"invoices": [{"id": "inv-a-001", "customer": "Acme Ltd", "amount": "1234.50", "currency": "EUR", "due_date": "2026-02-03", "status": "open"}],
 "totals": {"EUR": "1234.50", "USD": "99.00"}}
```

- Amounts are decimal strings with two places. `totals` sums `amount` per currency (only
  currencies present). Sort invoices by `due_date`, then `id`. Follow every page.
- Ledgerly answers `429` with a `Retry-After` header (seconds) when rate limiting. Wait that
  long and retry, up to 3 retries per request. Still limited → `503 upstream_rate_limited`.
- Bad key or account → `502 upstream_auth`.

`ledger` joins the integrations overview (kind `rest`).

## Ledgerly API

Base `{EXTERNAL}/ledger`. Header `X-Ledger-Key: <api_key>`.

`GET /v3/accounts/{account_id}/invoices?state=open|paid|all[&cursor=<c>]` →
`{"invoices": [{"invoice_id": ..., "customer_name": ..., "amount_cents": 123450, "currency": "EUR", "due": "2026-02-03", "state": "open"}], "cursor": "<c>" | null}`.
Bad key or account → `403`. Rate limited → `429` with `Retry-After`.
