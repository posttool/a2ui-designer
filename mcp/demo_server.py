"""Bundled demo "connected app": an in-process MCP server with fake data.

Served at `POST /mcp/demo` (Streamable HTTP, JSON responses; `?sse=1` answers
as an event stream to exercise that client path). Tools cover three verticals
from the Connected Apps platform (health, local & productivity, shopping) and
their arguments steer size classes and states: `limit` drives list length,
unknown ids return `isError`, more than 6 attendees yields no slots.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from typing import Any

SERVER_INFO = {"name": "dcb-demo-apps", "version": "1.0"}
PROTOCOL_VERSION = "2025-06-18"


def _h(*parts: Any) -> int:
  return int(
      hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:8], 16
  )


# ------------------------------------------------------------------ data
_MEDS = [
    ("Ibuprofen", "pain relief", ["200 mg", "400 mg", "600 mg"]),
    ("Acetaminophen", "pain relief", ["325 mg", "500 mg"]),
    ("Amoxicillin", "antibiotic", ["250 mg", "500 mg"]),
    ("Lisinopril", "blood pressure", ["5 mg", "10 mg", "20 mg"]),
    ("Metformin", "diabetes", ["500 mg", "850 mg", "1000 mg"]),
    ("Atorvastatin", "cholesterol", ["10 mg", "20 mg", "40 mg"]),
    ("Cetirizine", "allergy", ["5 mg", "10 mg"]),
    ("Omeprazole", "acid reflux", ["20 mg", "40 mg"]),
    ("Levothyroxine", "thyroid", ["25 mcg", "50 mcg", "75 mcg"]),
    ("Sertraline", "depression", ["25 mg", "50 mg", "100 mg"]),
    ("Albuterol inhaler", "asthma", ["90 mcg"]),
    ("Vitamin D3", "supplement", ["1000 IU", "2000 IU"]),
]
_STORES = [
    ("st_12", "Main St Pharmacy", "1420 Main St", 0.8),
    ("st_31", "Riverside Health", "88 River Rd", 2.3),
    ("st_47", "Oak Plaza Drugs", "5 Oak Plaza", 4.1),
]


def _medications() -> list[dict[str, Any]]:
  out = []
  for name, use, strengths in _MEDS:
    for s in strengths:
      h = _h(name, s)
      out.append({
          "id": f"med_{h % 100000:05d}",
          "name": name,
          "strength": s,
          "use": use,
          "form": "inhaler" if "inhaler" in name else "tablet",
          "price_usd": round(4 + (h % 4200) / 100, 2),
          "generic": h % 3 != 0,
          "in_stock": h % 5 != 0,
          "requires_prescription": (
              use not in ("pain relief", "allergy", "supplement")
          ),
          "image": (
              f"/img/{name.lower().replace(' ', '-')}-medicine-bottle/400x400"
          ),
      })
  return out


_PRESCRIPTIONS = {
    "RX-1001": ("Lisinopril", "10 mg", "Dr. Ana Ruiz", 3, 30),
    "RX-1002": ("Metformin", "500 mg", "Dr. Ana Ruiz", 0, 90),
    "RX-1003": ("Atorvastatin", "20 mg", "Dr. Sam Okafor", 5, 30),
    "RX-1004": ("Sertraline", "50 mg", "Dr. Lee Chen", 1, 30),
    "RX-1005": ("Albuterol inhaler", "90 mcg", "Dr. Lee Chen", 2, 1),
}

_PRODUCTS_BASE = [
    ("Trail running shoes", "Stride", 129.0, "shoes"),
    ("Waterproof hiking boots", "Summit", 189.0, "shoes"),
    ("Merino base layer", "Woolly", 79.0, "apparel"),
    ("Insulated jacket", "Summit", 249.0, "apparel"),
    ("Ultralight tent (2p)", "Camp+", 399.0, "camping"),
    ("Sleeping bag 20°F", "Camp+", 219.0, "camping"),
    ("Headlamp 400 lm", "Lumen", 39.0, "gear"),
    ("Insulated water bottle", "Hydra", 34.0, "gear"),
    ("Trekking poles", "Stride", 89.0, "gear"),
    ("Daypack 24 L", "Woolly", 119.0, "bags"),
]


def _products() -> list[dict[str, Any]]:
  out = []
  for i, (name, brand, price, cat) in enumerate(_PRODUCTS_BASE):
    for v, color in enumerate(("Graphite", "Forest", "Sand", "Ocean")):
      h = _h(name, color)
      out.append({
          "id": f"p_{i:02d}{v}",
          "name": f"{name} — {color}",
          "brand": brand,
          "category": cat,
          "price_usd": round(price * (0.85 + (h % 30) / 100), 2),
          "rating": round(3.4 + (h % 16) / 10, 1),
          "reviews": 12 + h % 2400,
          "in_stock": h % 7 != 0,
          "free_shipping": price > 50,
          "image": (
              f"/img/{name.lower().replace(' ', '-')}-{color.lower()}/600x600"
          ),
      })
  return out


_ORDERS = {
    "ORD-7781": ("p_000", 1, "delivered"),
    "ORD-7782": ("p_041", 2, "out_for_delivery"),
    "ORD-7783": ("p_060", 1, "processing"),
}


# ----------------------------------------------------------------- tools
def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict:
  s: dict[str, Any] = {"type": "object", "properties": props}
  if required:
    s["required"] = required
  return s


TOOLS: list[dict[str, Any]] = [
    {
        "name": "pharmacy_search_medications",
        "title": "Search medications",
        "description": (
            "Search the pharmacy catalog by drug name or condition. Returns"
            " matching products with price, stock and prescription status."
        ),
        "inputSchema": _obj(
            {
                "query": {
                    "type": "string",
                    "title": "Medication or condition",
                    "description": "e.g. ibuprofen, allergy, blood pressure",
                },
                "in_stock_only": {
                    "type": "boolean",
                    "title": "Only show in-stock items",
                    "default": False,
                },
                "limit": {
                    "type": "integer",
                    "title": "Max results",
                    "minimum": 1,
                    "maximum": 30,
                    "default": 8,
                },
            },
            ["query"],
        ),
    },
    {
        "name": "pharmacy_get_prescription",
        "title": "Get prescription",
        "description": "Look up one prescription by its number (e.g. RX-1001).",
        "inputSchema": _obj(
            {
                "prescription_id": {
                    "type": "string",
                    "title": "Prescription number",
                    "pattern": "^RX-[0-9]{4}$",
                    "examples": ["RX-1001"],
                }
            },
            ["prescription_id"],
        ),
    },
    {
        "name": "pharmacy_request_refill",
        "title": "Request a refill",
        "description": (
            "Request a refill for a prescription, for store pickup or home"
            " delivery. Fails when no refills remain."
        ),
        "inputSchema": _obj(
            {
                "prescription_id": {
                    "type": "string",
                    "title": "Prescription number",
                },
                "fulfillment": {
                    "type": "string",
                    "title": "How to get it",
                    "enum": ["pickup", "delivery"],
                    "default": "pickup",
                },
                "store_id": {
                    "type": "string",
                    "title": "Pickup store",
                    "enum": [s[0] for s in _STORES],
                    "description": "Required for pickup.",
                },
                "notes": {
                    "type": "string",
                    "title": "Notes for the pharmacist",
                    "maxLength": 500,
                },
            },
            ["prescription_id", "fulfillment"],
        ),
    },
    {
        "name": "calendar_find_slots",
        "title": "Find meeting times",
        "description": (
            "Find free slots when all attendees are available within a date"
            " range."
        ),
        "inputSchema": _obj(
            {
                "start_date": {
                    "type": "string",
                    "format": "date",
                    "title": "From",
                },
                "end_date": {"type": "string", "format": "date", "title": "To"},
                "duration_min": {
                    "type": "integer",
                    "title": "Duration (minutes)",
                    "enum": [15, 30, 45, 60, 90],
                    "default": 30,
                },
                "attendees": {
                    "type": "array",
                    "title": "Attendees",
                    "items": {"type": "string", "format": "email"},
                    "maxItems": 12,
                },
            },
            ["start_date", "end_date", "attendees"],
        ),
    },
    {
        "name": "calendar_book",
        "title": "Book a meeting",
        "description": "Book a slot returned by calendar_find_slots.",
        "inputSchema": _obj(
            {
                "slot_id": {"type": "string", "title": "Slot"},
                "title": {"type": "string", "title": "Meeting title"},
                "send_invites": {
                    "type": "boolean",
                    "title": "Send invites",
                    "default": True,
                },
            },
            ["slot_id", "title"],
        ),
    },
    {
        "name": "shop_search_products",
        "title": "Search products",
        "description": "Search the outdoor store catalog.",
        "inputSchema": _obj(
            {
                "query": {"type": "string", "title": "Search"},
                "category": {
                    "type": "string",
                    "title": "Category",
                    "enum": [
                        "any",
                        "shoes",
                        "apparel",
                        "camping",
                        "gear",
                        "bags",
                    ],
                    "default": "any",
                },
                "max_price": {
                    "type": "number",
                    "title": "Max price (USD)",
                    "minimum": 0,
                    "maximum": 1000,
                },
                "sort": {
                    "type": "string",
                    "title": "Sort by",
                    "enum": ["relevance", "price_low", "price_high", "rating"],
                    "default": "relevance",
                },
                "limit": {
                    "type": "integer",
                    "title": "Max results",
                    "minimum": 1,
                    "maximum": 40,
                    "default": 12,
                },
            },
            ["query"],
        ),
    },
    {
        "name": "shop_get_order",
        "title": "Track an order",
        "description": (
            "Get an order's items and shipping progress (e.g. ORD-7781)."
        ),
        "inputSchema": _obj(
            {"order_id": {"type": "string", "title": "Order number"}},
            ["order_id"],
        ),
    },
]

RESOURCES = [{
    "uri": "demo://pharmacy/stores",
    "name": "Pharmacy stores",
    "description": "Stores that support pickup.",
    "mimeType": "application/json",
}]


class ToolError(Exception):
  pass


def _search_meds(a: dict[str, Any]) -> dict[str, Any]:
  q = str(a.get("query") or "").strip().lower()
  if not q:
    raise ToolError("query is required")
  limit = max(1, min(30, int(a.get("limit") or 8)))
  items = [
      m
      for m in _medications()
      if q in m["name"].lower() or q in m["use"].lower() or q == "*"
  ]
  if a.get("in_stock_only"):
    items = [m for m in items if m["in_stock"]]
  return {
      "query": a.get("query"),
      "total": len(items),
      "results": items[:limit],
      "pickup_stores": [
          {"id": s[0], "name": s[1], "address": s[2], "distance_mi": s[3]}
          for s in _STORES
      ],
  }


def _prescription(pid: str) -> dict[str, Any]:
  p = _PRESCRIPTIONS.get(str(pid or "").upper())
  if not p:
    raise ToolError(f"No prescription found with number {pid!r}.")
  name, strength, doctor, refills, qty = p
  h = _h(pid)
  last = datetime.date(2026, 9, 1) + datetime.timedelta(days=h % 40)
  return {
      "prescription_id": str(pid).upper(),
      "medication": name,
      "strength": strength,
      "quantity": qty,
      "directions": "Take 1 by mouth daily" if qty > 1 else "2 puffs as needed",
      "prescriber": doctor,
      "refills_remaining": refills,
      "last_filled": last.isoformat(),
      "next_eligible": (last + datetime.timedelta(days=qty - 5)).isoformat(),
      "status": "active" if refills else "needs_renewal",
  }


def _refill(a: dict[str, Any]) -> dict[str, Any]:
  rx = _prescription(a.get("prescription_id"))
  if not rx["refills_remaining"]:
    raise ToolError(
        f"{rx['medication']} has no refills left. Ask {rx['prescriber']} to"
        " renew the prescription."
    )
  how = a.get("fulfillment") or "pickup"
  store = next((s for s in _STORES if s[0] == a.get("store_id")), _STORES[0])
  steps = [
      {"step": "Request received", "done": True},
      {"step": "Pharmacist review", "done": False, "eta": "within 2 hours"},
      {
          "step": "Ready for pickup" if how == "pickup" else "Out for delivery",
          "done": False,
          "eta": "today 5:00 PM" if how == "pickup" else "tomorrow by 8 PM",
      },
  ]
  return {
      "confirmation": f"RF-{_h(rx['prescription_id'], how) % 100000:05d}",
      "prescription": rx,
      "fulfillment": how,
      "store": (
          {"id": store[0], "name": store[1], "address": store[2]}
          if how == "pickup"
          else None
      ),
      "copay_usd": round(5 + _h(rx["medication"]) % 2000 / 100, 2),
      "notes": a.get("notes") or "",
      "timeline": steps,
  }


def _find_slots(a: dict[str, Any]) -> dict[str, Any]:
  att = [str(x) for x in a.get("attendees") or [] if x]
  if not att:
    raise ToolError("Add at least one attendee.")
  try:
    d0 = datetime.date.fromisoformat(str(a.get("start_date")))
    d1 = datetime.date.fromisoformat(str(a.get("end_date")))
  except ValueError as e:
    raise ToolError("Dates must be YYYY-MM-DD.") from e
  dur = int(a.get("duration_min") or 30)
  slots = []
  if len(att) <= 6:
    d = d0
    while d <= d1 and len(slots) < 12:
      if d.weekday() < 5:
        for hour in (9, 11, 14, 16):
          if _h(d, hour, len(att)) % 3:
            start = datetime.datetime(d.year, d.month, d.day, hour, 0)
            slots.append({
                "slot_id": f"slot_{d.strftime('%m%d')}_{hour}",
                "start": start.isoformat(),
                "end": (start + datetime.timedelta(minutes=dur)).isoformat(),
                "conflicts": [],
                "room": ["Atlas", "Birch", "Cedar"][_h(d, hour) % 3],
            })
      d += datetime.timedelta(days=1)
  return {
      "attendees": [
          {"email": e, "name": e.split("@")[0].replace(".", " ").title()}
          for e in att
      ],
      "duration_min": dur,
      "slots": slots[:12],
      "note": (
          "No time works for everyone; try fewer attendees or a wider range."
          if not slots
          else ""
      ),
  }


def _book(a: dict[str, Any]) -> dict[str, Any]:
  sid = str(a.get("slot_id") or "")
  if not sid.startswith("slot_"):
    raise ToolError("Unknown slot. Call calendar_find_slots first.")
  return {
      "event_id": f"evt_{_h(sid) % 1000000:06d}",
      "title": a.get("title") or "Meeting",
      "slot_id": sid,
      "invites_sent": bool(a.get("send_invites", True)),
      "meet_link": f"https://meet.example.com/{_h(sid) % 999:03d}-demo",
  }


def _search_products(a: dict[str, Any]) -> dict[str, Any]:
  q = str(a.get("query") or "").strip().lower()
  items = [
      p
      for p in _products()
      if not q
      or q == "*"
      or any(w in p["name"].lower() for w in q.split())
      or q in p["category"]
  ]
  cat = a.get("category") or "any"
  if cat != "any":
    items = [p for p in items if p["category"] == cat]
  if a.get("max_price") is not None:
    items = [p for p in items if p["price_usd"] <= float(a["max_price"])]
  sort = a.get("sort") or "relevance"
  if sort == "price_low":
    items.sort(key=lambda p: p["price_usd"])
  elif sort == "price_high":
    items.sort(key=lambda p: -p["price_usd"])
  elif sort == "rating":
    items.sort(key=lambda p: -p["rating"])
  limit = max(1, min(40, int(a.get("limit") or 12)))
  return {
      "query": a.get("query"),
      "total": len(items),
      "results": items[:limit],
  }


def _order(oid: str) -> dict[str, Any]:
  o = _ORDERS.get(str(oid or "").upper())
  if not o:
    raise ToolError(f"No order {oid!r} on this account.")
  pid, qty, status = o
  prod = next((p for p in _products() if p["id"] == pid), _products()[0])
  order = ["processing", "shipped", "out_for_delivery", "delivered"]
  idx = order.index(status)
  return {
      "order_id": str(oid).upper(),
      "status": status,
      "items": [{"product": prod, "quantity": qty}],
      "total_usd": round(prod["price_usd"] * qty, 2),
      "shipping": [
          {"step": s.replace("_", " ").capitalize(), "done": i <= idx}
          for i, s in enumerate(order)
      ],
      "carrier": "UPS",
      "tracking": f"1Z{_h(oid) % 10**10:010d}",
  }


HANDLERS = {
    "pharmacy_search_medications": _search_meds,
    "pharmacy_get_prescription": lambda a: _prescription(
        a.get("prescription_id")
    ),
    "pharmacy_request_refill": _refill,
    "calendar_find_slots": _find_slots,
    "calendar_book": _book,
    "shop_search_products": _search_products,
    "shop_get_order": lambda a: _order(a.get("order_id")),
}


# ------------------------------------------------------------- JSON-RPC
def _ok(rid: Any, result: Any) -> dict[str, Any]:
  return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid: Any, code: int, message: str) -> dict[str, Any]:
  return {
      "jsonrpc": "2.0",
      "id": rid,
      "error": {"code": code, "message": message},
  }


def handle_message(msg: dict[str, Any]) -> dict[str, Any] | None:
  """Handles one JSON-RPC message; returns None for notifications."""
  method = msg.get("method")
  rid = msg.get("id")
  params = msg.get("params") or {}
  if rid is None:
    return None
  if method == "initialize":
    return _ok(
        rid,
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}, "resources": {}},
            "serverInfo": SERVER_INFO,
            "instructions": (
                "Demo connected app for the A2UI Designer."
            ),
        },
    )
  if method == "ping":
    return _ok(rid, {})
  if method == "tools/list":
    # Two pages, to exercise cursor handling.
    if params.get("cursor") == "page2":
      return _ok(rid, {"tools": TOOLS[4:]})
    return _ok(rid, {"tools": TOOLS[:4], "nextCursor": "page2"})
  if method == "resources/list":
    return _ok(rid, {"resources": RESOURCES})
  if method == "resources/read":
    if params.get("uri") != RESOURCES[0]["uri"]:
      return _err(rid, -32002, "Resource not found")
    stores = [
        {"id": s[0], "name": s[1], "address": s[2], "distance_mi": s[3]}
        for s in _STORES
    ]
    return _ok(
        rid,
        {
            "contents": [{
                "uri": RESOURCES[0]["uri"],
                "mimeType": "application/json",
                "text": json.dumps(stores),
            }]
        },
    )
  if method == "tools/call":
    name = params.get("name")
    fn = HANDLERS.get(name)
    if not fn:
      return _err(rid, -32602, f"Unknown tool: {name}")
    try:
      data = fn(params.get("arguments") or {})
    except ToolError as e:
      return _ok(
          rid, {"content": [{"type": "text", "text": str(e)}], "isError": True}
      )
    except (TypeError, ValueError) as e:
      return _ok(
          rid,
          {
              "content": [{"type": "text", "text": f"Invalid arguments: {e}"}],
              "isError": True,
          },
      )
    return _ok(
        rid,
        {
            "content": [{"type": "text", "text": json.dumps(data)}],
            "structuredContent": data,
            "isError": False,
        },
    )
  return _err(rid, -32601, f"Method not found: {method}")


def handle_http(body: Any, sse: bool = False) -> tuple[int, bytes, str, dict]:
  """HTTP POST body → (status, bytes, content_type, headers)."""
  hdrs = {"Mcp-Session-Id": "demo-session"}
  if isinstance(body, list):
    replies = [r for r in (handle_message(m) for m in body) if r]
  elif isinstance(body, dict):
    r = handle_message(body)
    replies = [r] if r else []
  else:
    replies = [_err(None, -32700, "Parse error")]
  if not replies:
    return 202, b"", "application/json", hdrs
  payload = replies if isinstance(body, list) else replies[0]
  if sse:
    data = "event: message\ndata: " + json.dumps(payload) + "\n\n"
    return 200, data.encode(), "text/event-stream", hdrs
  return 200, json.dumps(payload).encode(), "application/json", hdrs
