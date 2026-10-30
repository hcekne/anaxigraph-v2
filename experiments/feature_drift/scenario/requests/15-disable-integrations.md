# Request 15 — Switching integrations off

Security policies differ per customer. Northwind's IT forbids the CRM entirely, and someone
leaked a token last week. Workspaces need to switch integrations off, and remove credentials.

## What I need

Setting `disabled_integrations`: a list of integration names (unknown names → `422 invalid_request`).
Default `[]`. For a disabled integration in that workspace:

- The overview shows `"status": "disabled"` and does no live check.
- Its capabilities show `"available": false`. Its endpoints and capability invocations return
  `403 integration_disabled` and contact no provider. Workflow steps using it fail with that code.
- Its stored credentials are kept (re-enabling restores everything) and usage is not charged.

`DELETE /api/credentials/{integration}` → `204`, removes that workspace's credentials (status
becomes `unconfigured`). Unknown integration → `404 not_found`.
