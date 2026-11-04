# Request 13 — Webhook & Event Sink Transports with Idempotency Keys

Emit deterministic outbound notifications whenever a speculative branch is promoted or a retroactive taint invalidates a sealed grant.

Downstream systems want to hear about promotions and about sealed payouts that just became
doubtful. They dedupe on an idempotency key, and their endpoints are sometimes slow or down;
that must never slow down or block our ledger.

## What I need

**Sinks** (per tenant): `POST /api/sinks` with
`{"id": "erp", "url": "http://...", "secret": "<non-empty>", "events": ["branch.promoted"]}`
(`events` optional, default both event types) → `201 {"id", "url", "events"}` (never the
secret). `id` matches `[A-Za-z0-9][A-Za-z0-9._:-]{0,63}` (duplicate → `409 conflict`); `url`
is an absolute `http://` or `https://` URL; unknown event type → `422 invalid_request`.
`GET /api/sinks` → `{"sinks": [{"id", "url", "events"}, ...]}` sorted by id, `DELETE /api/sinks/{id}` → `204`, unknown → `404 not_found`.

**Events.** Bodies are exactly:

- after a successful merge:
  `{"event": "branch.promoted", "tenant": "<tenant>", "branch": "<name>", "asserted_at": T, "promoted": [ids in canonical order]}`
- after a retraction of X at `T`, for every **sealed grant** that X's retraction newly taints
  (it was not tainted just before and is just after): `{"event": "grant.invalidated", "tenant": "<tenant>", "assertion_id": "<grant id>", "seal_ids": [covering seal ids, sorted], "cause": "X", "asserted_at": T}`.
  A sealed grant is an assertion on `main` whose key starts with `grant:` and whose
  `effective_at` lies inside at least one seal existing at `T`. One event per grant, in
  canonical order of the grants, however many causal paths lead to it.

Each event goes to every sink of the tenant subscribed to its type, as of when the write
happened.

**Delivery.** `POST <url>` with the canonical JSON of the event as body and headers
`Content-Type: application/json`, `X-ChronoWeave-Event: <event>`,
`Idempotency-Key: <SHA-256 of the body bytes>` and
`X-ChronoWeave-Signature: sha256=<HMAC-SHA256 of the body bytes keyed with the sink secret>`.

- Delivery happens after the write has been committed and never delays the response: a sink
  that takes seconds to answer must not slow the write down.
- First attempt within 2 seconds of the write. A response other than 2xx, a timeout (5 s) or a
  connection error → retry after 1, 2, 4 and 8 seconds (5 attempts in all), every attempt with
  the same body and key; then give up.
- `GET /api/sinks/{id}/deliveries` → `{"deliveries": [{"idempotency_key", "event",
  "status": "pending" | "delivered" | "failed", "attempts": n, "last_status": <HTTP status or null>}]}`
  in the order the events were emitted.

The same history always produces the same event bodies and keys.
