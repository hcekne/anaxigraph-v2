"""Deterministic fake external services for the feature-drift benchmark.

One threaded HTTP server plays every third-party provider the requests mention. Candidates
never see this file; they only get the provider documentation inside each request.
Hidden tests drive it through `/_hub/*` control endpoints:

- `POST /_hub/reset` clears call logs, created records, fault injection and tokens.
- `POST /_hub/era {"step": n}` sets provider evolution: WeatherCo v1 forecasts retire at
  step 11 and MCP servers require protocol sessions and structured results from step 20.
- `POST /_hub/configure {"service": name, ...}` injects faults: `fail_next`, `fail_status`,
  `rate_limit_next`, `retry_after`, `latency`, `expire_tokens`, `expire_sessions`.
- `GET /_hub/calls?service=name` lists received calls.

Run standalone with `python hub.py --port 8765`.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE_DATE = "2026-01-15"
CITIES = {
    "oslo": ("Oslo", "NO", 59.91, 10.75),
    "london": ("London", "GB", 51.51, -0.13),
    "paris": ("Paris", "FR", 48.86, 2.35),
    "berlin": ("Berlin", "DE", 52.52, 13.40),
    "geneva": ("Geneva", "CH", 46.20, 6.14),
    "tokyo": ("Tokyo", "JP", 35.68, 139.69),
    "denver": ("Denver", "US", 39.74, -104.99),
    "tromso": ("Tromso", "NO", 69.65, 18.96),
}
CONDITIONS = ["Clear", "Partly cloudy", "Overcast", "Light snow", "Heavy snow", "Rain"]

WEATHERCO_KEYS = {"wc-key-1", "wc-key-2"}
SKYCAST_TOKENS = {"sky-token-1"}
CRM_TOKENS = {"crm-token-a": "a", "crm-token-b": "b"}
DOCS_TOKENS = {"docs-token"}
TICKETS_TOKENS = {"tickets-token-a": "a", "tickets-token-b": "b"}
CALENDAR_CLIENTS = {("cal-client-a", "cal-secret-a"): "a", ("cal-client-b", "cal-secret-b"): "b"}
LEDGER_KEYS = {("ledger-key-a", "acc-100"): "a", ("ledger-key-b", "acc-200"): "b"}
FX_APPS = {"fx-app-1"}
LINGO_TOKENS = {"lingo-token"}
SHIP_USERS = {("ship-user", "ship-pass")}
GEO_KEYS = {"geo-key-1"}
NOTES_TOKENS = {"notes-token"}

MCP_OLD, MCP_NEW = "2025-06-18", "2026-03-26"
RATES = {"USD": "1", "EUR": "0.92", "GBP": "0.79", "NOK": "10.85", "JPY": "151.20", "CHF": "0.88"}


def _h(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


def forecast(city_key: str, days: int) -> list[dict]:
    """Daily weather in Celsius with one decimal; every provider derives from this."""
    rows = []
    for index in range(days):
        h = _h(city_key, index)
        high = round(-6 + (h % 160) / 10, 1)
        low = round(high - 2 - (h // 7 % 60) / 10, 1)
        precip = round((h // 11 % 90) / 10, 1)
        day = 15 + index
        rows.append(
            {
                "date": f"2026-01-{day:02d}",
                "high_c": high,
                "low_c": low,
                "precip_mm": precip,
                "summary": CONDITIONS[h // 13 % len(CONDITIONS)],
            }
        )
    return rows


FIRST = ["Ann", "Bjorn", "Carla", "Dev", "Elif", "Farah", "Gus", "Hana", "Ivo", "Jun", "Kari"]
LAST = ["Berg", "Moss", "Lund", "Okafor", "Silva", "Tanaka", "Weber", "Ng", "Haug", "Ruiz"]
ORGS = ["Acme Ltd", "Fjord AS", None, "Globex", "Initech"]


def contacts(dataset: str) -> list[dict]:
    count = 25 if dataset == "a" else 8
    rows = []
    for index in range(count):
        h = _h("crm", dataset, index)
        first, last = FIRST[h % len(FIRST)], LAST[h // 17 % len(LAST)]
        org = ORGS[h // 5 % len(ORGS)] if dataset == "a" else "Northwind Traders"
        rows.append(
            {
                "id": f"c_{dataset}{index + 1:03d}",
                "full_name": f"{first} {last}",
                "email": f"{first.lower()}.{last.lower()}{index}@{dataset}-example.com",
                "org": {"name": org} if org else None,
            }
        )
    return rows


DOCS = [
    ("doc-1", "Deploying the API", "Use the deploy pipeline to release the API to production."),
    ("doc-2", "Rollback guide", "To roll back a deploy, promote the previous release tag."),
    ("doc-3", "On-call handbook", "Pages go to the primary on-call engineer first."),
    ("doc-4", "Database migrations", "Run migrations before deploying application code."),
    ("doc-5", "Incident review", "Write the incident review within five working days."),
    ("doc-6", "Access requests", "Request production access through the access portal."),
    ("doc-7", "Feature flags", "Flags let us deploy dark and release gradually."),
    ("doc-8", "Cost tagging", "Every cloud resource needs an owner and cost tag."),
    ("doc-9", "Secrets rotation", "Rotate provider secrets every ninety days."),
    ("doc-10", "Load testing", "Load test before large launches and deploy windows."),
    ("doc-11", "API style", "Errors use a JSON envelope with a stable code."),
    ("doc-12", "Release calendar", "Freeze deploys during the last week of December."),
]


def events(dataset: str) -> list[dict]:
    rows = []
    for index in range(14 if dataset == "a" else 6):
        h = _h("cal", dataset, index)
        day, hour = 10 + index % 12, 7 + h % 9
        rows.append(
            {
                "uid": f"ev-{dataset}-{index + 1}",
                "summary": ["Site visit", "Boiler service", "Crew briefing", "Roof check"][h % 4]
                + f" #{index + 1}",
                "starts_at": f"2026-01-{day:02d}T{hour:02d}:00:00Z",
                "ends_at": f"2026-01-{day:02d}T{hour + 2:02d}:00:00Z",
                "where": ["Depot", "North wing", None, "Main hall"][h // 3 % 4],
            }
        )
    return rows


def invoices(dataset: str) -> list[dict]:
    rows = []
    for index in range(23 if dataset == "a" else 5):
        h = _h("ledger", dataset, index)
        rows.append(
            {
                "invoice_id": f"inv-{dataset}-{index + 1:03d}",
                "customer_name": ["Acme Ltd", "Fjord AS", "Globex", "Initech"][h % 4],
                "amount_cents": 1000 + h % 250000,
                "currency": "EUR" if h % 3 else "USD",
                "due": f"2026-02-{1 + h % 27:02d}",
                "state": "open" if h % 5 < 3 else "paid",
            }
        )
    return rows


SHIPMENTS = {
    "SH123": (
        "in_transit",
        [
            ("2026-01-12T08:00:00Z", "Oslo", "Picked up"),
            ("2026-01-13T14:30:00Z", "Gothenburg", "In transit"),
        ],
    ),
    "SH456": (
        "delivered",
        [
            ("2026-01-10T09:00:00Z", "Berlin", "Picked up"),
            ("2026-01-11T16:10:00Z", "Paris", "Out for delivery"),
            ("2026-01-11T18:45:00Z", "Paris", "Delivered"),
        ],
    ),
}
TRANSLATIONS = {
    ("hello", "es"): "hola",
    ("hello", "fr"): "bonjour",
    ("thank you", "es"): "gracias",
    ("good morning", "de"): "guten Morgen",
}

TOOLS = {
    "docs": [
        ("search_docs", "Full-text search over engineering docs", ["query"], ["limit"]),
        ("get_doc", "Fetch one document by id", ["id"], []),
    ],
    "tickets": [
        ("create_ticket", "Open a ticket", ["title", "body", "priority"], []),
        ("list_tickets", "List tickets by status", [], ["status"]),
    ],
    "lingo": [("translate", "Translate text", ["text", "target_lang"], [])],
    "notes": [
        ("create_note", "Create a note", ["title", "text"], []),
        ("search_notes", "Search notes", ["query"], []),
    ],
}
MCP_TOKENS = {
    "docs": {t: "a" for t in DOCS_TOKENS},
    "tickets": TICKETS_TOKENS,
    "lingo": {t: "a" for t in LINGO_TOKENS},
    "notes": {t: "a" for t in NOTES_TOKENS},
}


class Problem(Exception):
    def __init__(self, status: int, body, headers: dict | None = None):
        self.status, self.body, self.headers = status, body, headers or {}


class Hub:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.step = 1
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.calls: list[dict] = []
            self.faults: dict[str, dict] = {}
            # Issued tokens and MCP sessions persist like real provider state; tests that
            # need expiry clear them explicitly.
            self.tokens: dict[str, str] = getattr(self, "tokens", {})
            self.sessions: dict[str, str] = getattr(self, "sessions", {})
            self.created_contacts: dict[str, list[dict]] = {"a": [], "b": []}
            self.tickets: dict[str, list[dict]] = {"a": [], "b": []}
            self.notes: list[dict] = []
            self.counter = 1000

    # -- request plumbing ---------------------------------------------------------------
    def handle(self, method: str, raw_path: str, headers: dict, body: bytes):
        parsed = urlparse(raw_path)
        path, query = parsed.path, {k: v[-1] for k, v in parse_qs(parsed.query).items()}
        if path.startswith("/_hub/"):
            return self.control(method, path, query, body)
        parts = [p for p in path.split("/") if p]
        service = parts[1] if parts[:1] == ["mcp"] and len(parts) > 1 else (parts or [""])[0]
        try:
            payload = json.loads(body) if body else None
        except ValueError:
            payload = None
        with self.lock:
            self.calls.append(
                {
                    "service": service,
                    "method": method,
                    "path": path,
                    "query": query,
                    "headers": {k.lower(): v for k, v in headers.items()},
                    "body": payload if payload is not None else body.decode(errors="replace"),
                    "at": time.time(),
                }
            )
            fault = self.faults.get(service, {})
            delay = fault.get("latency", 0)
        if delay:
            time.sleep(delay)
        with self.lock:
            if fault.get("fail_next", 0) > 0:
                fault["fail_next"] -= 1
                return fault.get("fail_status", 500), {"error": "injected failure"}, {}
            if fault.get("rate_limit_next", 0) > 0:
                fault["rate_limit_next"] -= 1
                retry = str(fault.get("retry_after", 1))
                return 429, {"error": "rate_limited"}, {"Retry-After": retry}
        is_mcp = parts[:1] == ["mcp"] and service in MCP_TOKENS
        handler = getattr(self, f"svc_{service}", None)
        if not is_mcp and not callable(handler):
            return 404, {"error": "unknown service"}, {}
        try:
            if is_mcp:
                if method != "POST":
                    return 405, {"error": "use POST"}, {}
                return self.mcp(service, headers, payload)
            return handler(method, parts[1:], query, headers, payload, body)
        except Problem as problem:
            return problem.status, problem.body, problem.headers

    def control(self, method, path, query, body):
        data = json.loads(body) if body else {}
        if path == "/_hub/reset":
            self.reset()
            return 200, {"ok": True}, {}
        if path == "/_hub/era":
            self.step = int(data["step"])
            return 200, {"step": self.step}, {}
        if path == "/_hub/configure":
            service = data.pop("service")
            with self.lock:
                if data.pop("expire_tokens", False):
                    self.tokens = {k: v for k, v in self.tokens.items() if v != service}
                if data.pop("expire_sessions", False):
                    self.sessions = {k: v for k, v in self.sessions.items() if v != service}
                self.faults.setdefault(service, {}).update(data)
            return 200, {"ok": True}, {}
        if path == "/_hub/calls":
            with self.lock:
                rows = [c for c in self.calls if query.get("service") in (None, c["service"])]
            return 200, {"calls": rows}, {}
        if path == "/_hub/health":
            return 200, {"ok": True}, {}
        return 404, {"error": "unknown control"}, {}

    def next_id(self) -> int:
        with self.lock:
            self.counter += 1
            return self.counter

    @staticmethod
    def header(headers: dict, name: str) -> str:
        return next((v for k, v in headers.items() if k.lower() == name.lower()), "")

    def bearer(self, headers) -> str:
        value = self.header(headers, "Authorization")
        return value[7:] if value.startswith("Bearer ") else ""

    # -- REST providers -------------------------------------------------------------------
    @staticmethod
    def city(name: str | None):
        return CITIES.get((name or "").strip().lower())

    def svc_weatherco(self, method, parts, query, headers, payload, body):
        if self.header(headers, "X-Api-Key") not in WEATHERCO_KEYS:
            raise Problem(401, {"message": "invalid key"})
        days = int(query.get("days", 3))
        if parts == ["v1", "geocode"]:
            found = [
                {"name": c[0], "country": c[1], "lat": c[2], "lon": c[3]}
                for key, c in CITIES.items()
                if key == query.get("q", "").strip().lower()
            ]
            return 200, {"results": found}, {}
        if parts == ["v1", "forecast"]:
            if self.step >= 11:
                raise Problem(410, {"message": "v1 retired; use v2"})
            match = next(
                (
                    key
                    for key, c in CITIES.items()
                    if abs(c[2] - float(query["lat"])) < 0.01
                    and abs(c[3] - float(query["lon"])) < 0.01
                ),
                None,
            )
            if match is None:
                raise Problem(404, {"message": "no data for location"})
            daily = [
                {
                    "date": r["date"],
                    "temp_max_c": r["high_c"],
                    "temp_min_c": r["low_c"],
                    "precip_mm": r["precip_mm"],
                    "conditions": r["summary"],
                }
                for r in forecast(match, days)
            ]
            return 200, {"location": {"lat": query["lat"], "lon": query["lon"]}, "daily": daily}, {}
        if parts == ["v2", "forecast"]:
            city = self.city(query.get("q"))
            if city is None:
                raise Problem(404, {"error": "place_not_found"})
            key = query["q"].strip().lower()
            rows = []
            for r in forecast(key, days):
                snow = r["precip_mm"] if r["high_c"] < 1 else 0.0
                rows.append(
                    {
                        "day": r["date"],
                        "t_max_k": round(r["high_c"] + 273.15, 2),
                        "t_min_k": round(r["low_c"] + 273.15, 2),
                        "rain_mm": round(r["precip_mm"] - snow, 1),
                        "snow_mm": snow,
                        "text": r["summary"],
                    }
                )
            return 200, {"place": {"name": city[0], "country_code": city[1]}, "forecast": rows}, {}
        raise Problem(404, {"message": "not found"})

    def svc_skycast(self, method, parts, query, headers, payload, body):
        if self.bearer(headers) not in SKYCAST_TOKENS:
            raise Problem(401, {"error": "unauthorized"})
        if method != "POST" or parts != ["api", "forecast"] or not isinstance(payload, dict):
            raise Problem(400, {"error": "bad request"})
        city = self.city(payload.get("location"))
        if city is None:
            raise Problem(404, {"error": "unknown location"})
        rows = [
            {
                "date": r["date"],
                "max": r["high_c"],
                "min": r["low_c"],
                "precipitation": r["precip_mm"],
                "description": r["summary"],
            }
            for r in forecast(payload["location"].strip().lower(), int(payload.get("days", 3)))
        ]
        return 200, {"city": city[0], "country": city[1], "days": rows}, {}

    def svc_crm(self, method, parts, query, headers, payload, body):
        value = self.header(headers, "Authorization")
        dataset = CRM_TOKENS.get(value[6:] if value.startswith("Token ") else "")
        if dataset is None:
            raise Problem(401, {"error": "invalid token"})
        if parts != ["api", "contacts"]:
            raise Problem(404, {"error": "not found"})
        rows = contacts(dataset) + self.created_contacts[dataset]
        if method == "POST":
            if not isinstance(payload, dict) or not payload.get("email"):
                raise Problem(400, {"error": "email required"})
            if any(r["email"].lower() == payload["email"].lower() for r in rows):
                raise Problem(409, {"error": "duplicate_email"})
            org = payload.get("org_name")
            row = {
                "id": f"c_{dataset}n{self.next_id()}",
                "full_name": payload.get("full_name", ""),
                "email": payload["email"],
                "org": {"name": org} if org else None,
            }
            self.created_contacts[dataset].append(row)
            return 201, {"data": row}, {}
        term = query.get("search", "").lower()
        if term:
            rows = [
                r
                for r in rows
                if term in r["full_name"].lower()
                or term in r["email"].lower()
                or term in ((r["org"] or {}).get("name") or "").lower()
            ]
        page = int(query.get("page", 1))
        chunk = rows[(page - 1) * 10 : page * 10]
        more = page * 10 < len(rows)
        return 200, {"data": chunk, "page": page, "next_page": page + 1 if more else None}, {}

    def svc_calendar(self, method, parts, query, headers, payload, body):
        if parts == ["oauth", "token"]:
            form = payload if isinstance(payload, dict) else {}
            if not form and body:
                form = {k: v[-1] for k, v in parse_qs(body.decode()).items()}
            dataset = CALENDAR_CLIENTS.get((form.get("client_id"), form.get("client_secret")))
            if form.get("grant_type") != "client_credentials" or dataset is None:
                raise Problem(401, {"error": "invalid_client"})
            token = "cal-" + uuid.uuid4().hex[:12]
            with self.lock:
                self.tokens[token] = "calendar"
                self.tokens[token + ":dataset"] = dataset
            return 200, {"access_token": token, "token_type": "Bearer", "expires_in": 3600}, {}
        token = self.bearer(headers)
        with self.lock:
            valid = self.tokens.get(token) == "calendar"
            dataset = self.tokens.get(token + ":dataset")
        if not valid:
            raise Problem(401, {"error": "token_expired"})
        if parts != ["v1", "events"]:
            raise Problem(404, {"error": "not found"})
        start, end = query.get("start", "0000"), query.get("end", "9999")
        rows = sorted(
            (e for e in events(dataset) if start <= e["starts_at"][:10] <= end),
            key=lambda e: e["uid"],
        )
        offset = int(query.get("cursor", 0))
        chunk = rows[offset : offset + 5]
        cursor = str(offset + 5) if offset + 5 < len(rows) else None
        return 200, {"items": chunk, "next": cursor}, {}

    def svc_ledger(self, method, parts, query, headers, payload, body):
        if len(parts) != 4 or parts[:2] != ["v3", "accounts"] or parts[3] != "invoices":
            raise Problem(404, {"error": "not found"})
        dataset = LEDGER_KEYS.get((self.header(headers, "X-Ledger-Key"), parts[2]))
        if dataset is None:
            raise Problem(403, {"error": "forbidden"})
        state = query.get("state", "all")
        rows = [r for r in invoices(dataset) if state == "all" or r["state"] == state]
        offset = int(query.get("cursor", 0) or 0)
        chunk = rows[offset : offset + 10]
        cursor = str(offset + 10) if offset + 10 < len(rows) else None
        return 200, {"invoices": chunk, "cursor": cursor}, {}

    def svc_fxrates(self, method, parts, query, headers, payload, body):
        if query.get("app_id") not in FX_APPS:
            raise Problem(401, {"error": "invalid_app_id"})
        if parts != ["latest.json"]:
            raise Problem(404, {"error": "not found"})
        return 200, {"base": "USD", "timestamp": 1768435200, "rates": RATES}, {}

    def svc_shiptrack(self, method, parts, query, headers, payload, body):
        auth = self.header(headers, "Authorization")
        try:
            user, _, password = base64.b64decode(auth[6:]).decode().partition(":")
        except ValueError:
            user, password = "", ""
        if not auth.startswith("Basic ") or (user, password) not in SHIP_USERS:
            raise Problem(401, {"error": "unauthorized"})
        if len(parts) != 3 or parts[:2] != ["v2", "track"]:
            raise Problem(404, {"error": "not found"})
        shipment = SHIPMENTS.get(parts[2])
        if shipment is None:
            raise Problem(404, {"error": "unknown_tracking_code"})
        state, history = shipment
        rows = [{"ts": t, "place": p, "text": d} for t, p, d in history]
        return 200, {"tracking_code": parts[2], "state": state, "events": rows}, {}

    def svc_geo(self, method, parts, query, headers, payload, body):
        if self.header(headers, "X-Geo-Key") not in GEO_KEYS:
            raise Problem(401, {"error": "unauthorized"})
        if parts != ["v1", "search"]:
            raise Problem(404, {"error": "not found"})
        city = self.city(query.get("address", "").split(",")[-1])
        matches = [] if city is None else [{"lat": city[2], "lng": city[3], "label": city[0]}]
        return 200, {"matches": matches}, {}

    # -- MCP providers ----------------------------------------------------------------------
    def mcp(self, server: str, headers: dict, message):
        dataset = MCP_TOKENS[server].get(self.bearer(headers))
        if dataset is None:
            raise Problem(401, {"error": "unauthorized"})
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return 400, rpc_error(None, -32600, "Invalid Request"), {}
        method, ident = message.get("method", ""), message.get("id")
        params = message.get("params") or {}
        session_era = self.step >= 20
        if method.startswith("notifications/"):
            return 202, None, {}
        if method == "initialize":
            version = params.get("protocolVersion")
            if session_era and version != MCP_NEW:
                return 400, rpc_error(ident, -32602, f"Unsupported protocol version {version}"), {}
            session = uuid.uuid4().hex
            with self.lock:
                self.sessions[session] = server
            result = {
                "protocolVersion": MCP_NEW if session_era else MCP_OLD,
                "serverInfo": {"name": f"{server}-server", "version": "1.0"},
                "capabilities": {"tools": {}},
            }
            return (
                200,
                {"jsonrpc": "2.0", "id": ident, "result": result},
                {"Mcp-Session-Id": session},
            )
        if session_era:
            session = self.header(headers, "Mcp-Session-Id")
            if not session:
                return 400, rpc_error(ident, -32000, "Missing Mcp-Session-Id"), {}
            with self.lock:
                known = self.sessions.get(session) == server
            if not known:
                return 404, rpc_error(ident, -32001, "Session expired"), {}
        if method == "tools/list":
            tools = [
                {
                    "name": name,
                    "description": text,
                    "inputSchema": {
                        "type": "object",
                        "properties": {p: {"type": "string"} for p in required + optional},
                        "required": required,
                    },
                }
                for name, text, required, optional in TOOLS[server]
            ]
            return 200, {"jsonrpc": "2.0", "id": ident, "result": {"tools": tools}}, {}
        if method != "tools/call":
            return 200, rpc_error(ident, -32601, "Method not found"), {}
        name, arguments = params.get("name"), params.get("arguments") or {}
        try:
            value = getattr(self, f"tool_{server}")(name, arguments, dataset)
            failed = False
        except Problem as problem:
            value, failed = {"error": problem.body}, True
        if session_era:
            text = "Error: see structuredContent" if failed else "See structuredContent"
            result = {
                "content": [{"type": "text", "text": text}],
                "structuredContent": value,
                "isError": failed,
            }
        else:
            text = f"Error: {value['error']}" if failed else json.dumps(value)
            result = {"content": [{"type": "text", "text": text}], "isError": failed}
        return 200, {"jsonrpc": "2.0", "id": ident, "result": result}, {}

    def tool_docs(self, name, arguments, dataset):
        if name == "search_docs":
            term = str(arguments.get("query", "")).lower()
            limit = int(arguments.get("limit", 10))
            found = [d for d in DOCS if term in d[1].lower() or term in d[2].lower()]
            return {
                "results": [
                    {
                        "id": d[0],
                        "title": d[1],
                        "snippet": d[2][:40],
                        "url": f"https://docs.internal/{d[0]}",
                    }
                    for d in found[:limit]
                ]
            }
        if name == "get_doc":
            doc = next((d for d in DOCS if d[0] == arguments.get("id")), None)
            if doc is None:
                raise Problem(404, "not_found")
            return {
                "id": doc[0],
                "title": doc[1],
                "body": doc[2],
                "url": f"https://docs.internal/{doc[0]}",
            }
        raise Problem(400, "unknown_tool")

    def tool_tickets(self, name, arguments, dataset):
        if name == "create_ticket":
            if arguments.get("priority") not in {"low", "normal", "high"}:
                raise Problem(400, "invalid_priority")
            number = self.next_id()
            ticket = {
                "id": f"T-{number}",
                "title": arguments.get("title", ""),
                "body": arguments.get("body", ""),
                "priority": arguments["priority"],
                "status": "open",
                "url": f"https://tickets.internal/T-{number}",
            }
            self.tickets[dataset].append(ticket)
            return {"ticket": {k: ticket[k] for k in ("id", "url", "status")}}
        if name == "list_tickets":
            status = arguments.get("status")
            rows = [t for t in self.tickets[dataset] if status in (None, t["status"])]
            return {
                "tickets": [{k: t[k] for k in ("id", "title", "priority", "status")} for t in rows]
            }
        raise Problem(400, "unknown_tool")

    def tool_lingo(self, name, arguments, dataset):
        if name != "translate":
            raise Problem(400, "unknown_tool")
        text, target = str(arguments.get("text", "")), str(arguments.get("target_lang", ""))
        if not target:
            raise Problem(400, "target_lang required")
        translated = TRANSLATIONS.get((text.lower(), target), f"[{target}] {text}")
        return {"translation": translated, "source_lang": "en"}

    def tool_notes(self, name, arguments, dataset):
        if name == "create_note":
            note = {
                "id": f"N-{self.next_id()}",
                "title": arguments.get("title", ""),
                "text": arguments.get("text", ""),
            }
            self.notes.append(note)
            return {"note": note}
        if name == "search_notes":
            term = str(arguments.get("query", "")).lower()
            return {"notes": [n for n in self.notes if term in (n["title"] + n["text"]).lower()]}
        raise Problem(400, "unknown_tool")


def rpc_error(ident, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}


def make_handler(hub: Hub):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:
            pass

        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            status, payload, headers = hub.handle(
                self.command, self.path, dict(self.headers.items()), body
            )
            data = b"" if payload is None else json.dumps(payload).encode()
            self.send_response(status)
            if payload is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _serve

    return Handler


def start(port: int = 0) -> tuple[ThreadingHTTPServer, Hub]:
    hub = Hub()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(hub))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, hub


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the fake provider hub")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server, _ = start(args.port)
    print(f"hub on http://127.0.0.1:{server.server_address[1]}", flush=True)
    threading.Event().wait()
