"""Minimal MCP server for the Grand Meridian's hotel tools.

Fronts two tools behind a real MCP endpoint so Agent Manager can register it
as an identity-secured MCP proxy and gate access to book_room separately from
check_room_availability via scopes.

Deployed as its own Agent Manager component (same buildpack pipeline as the
other four in this repo), not tunneled from a laptop — see the module-level
PUBLIC_HOST comment below for why that distinction matters here.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from hotel_data import ROOMS

# The public hostname this service is reachable at — required by the MCP
# SDK's own DNS-rebinding protection, a check separate from Agent Manager's
# own SSRF guard on the registered MCP proxy's upstream URL. Set via env var
# rather than hardcoded, since the same image runs behind whichever gateway
# domain the deploying instance uses (AWS's public sslip.io domain here;
# a local instance's .localhost domain if ever redeployed there).
PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "localhost")
PUBLIC_SCHEME = os.environ.get("PUBLIC_SCHEME", "https")

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_BOOKINGS: dict[str, dict[str, Any]] = {}

mcp = MCPServer("hotel-tools")


@mcp.tool()
def check_room_availability(
    room_type: str,
    check_in: str | None = None,
    nights: int | None = None,
) -> dict[str, Any]:
    """Check availability and price for hotel rooms.

    Args:
        room_type: One of honeymoon, deluxe, standard, junior, presidential.
        check_in: Check-in date in ISO format (YYYY-MM-DD), if known.
        nights: Number of nights. Defaults to 1.
    """
    if not isinstance(room_type, str) or room_type not in ROOMS:
        return {"error": f"Unknown room type. Available types: {', '.join(ROOMS.keys())}."}
    if check_in is not None and not _ISO_DATE.match(check_in):
        return {"error": "Check-in date must be in YYYY-MM-DD format."}
    n = 1 if nights is None else nights
    if not isinstance(n, int) or n < 1 or n > 30:
        return {"error": "Nights must be an integer between 1 and 30."}

    room = ROOMS[room_type]
    return {
        "room_type": room_type,
        "name": room["name"],
        "price_per_night_usd": room["price_per_night_usd"],
        "nights": n,
        "total_usd": room["price_per_night_usd"] * n,
        "size_sqft": room["size_sqft"],
        "description": room["description"],
        "available": True,
        "check_in": check_in,
    }


@mcp.tool()
def book_room(
    room_type: str,
    check_in: str,
    nights: int,
    guest_name: str,
) -> dict[str, Any]:
    """Book a hotel room for a guest.

    Args:
        room_type: One of honeymoon, deluxe, standard, junior, presidential.
        check_in: Check-in date in ISO format (YYYY-MM-DD).
        nights: Number of nights, between 1 and 30.
        guest_name: Name to book the room under.
    """
    if not isinstance(room_type, str) or room_type not in ROOMS:
        return {"error": f"Unknown room type. Available types: {', '.join(ROOMS.keys())}."}
    if not isinstance(check_in, str) or not _ISO_DATE.match(check_in):
        return {"error": "check_in must be in YYYY-MM-DD format."}
    if not isinstance(nights, int) or nights < 1 or nights > 30:
        return {"error": "nights must be an integer between 1 and 30."}
    if not isinstance(guest_name, str) or not guest_name.strip():
        return {"error": "guest_name is required."}

    room = ROOMS[room_type]
    confirmation_id = f"GM-{uuid.uuid4().hex[:8].upper()}"
    booking = {
        "confirmation_id": confirmation_id,
        "room_type": room_type,
        "name": room["name"],
        "check_in": check_in,
        "nights": nights,
        "total_usd": room["price_per_night_usd"] * nights,
        "guest_name": guest_name.strip(),
        "status": "confirmed",
    }
    _BOOKINGS[confirmation_id] = booking
    return booking


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app = mcp.streamable_http_app(
        host="0.0.0.0",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[PUBLIC_HOST, "localhost", f"localhost:{port}", f"127.0.0.1:{port}"],
            allowed_origins=[f"{PUBLIC_SCHEME}://{PUBLIC_HOST}"],
        ),
    )
    uvicorn.run(app, host="0.0.0.0", port=port)
