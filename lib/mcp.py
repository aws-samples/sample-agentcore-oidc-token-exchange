"""Minimal MCP (JSON-RPC over HTTP) client for the Gateway."""

import json
import urllib.error
import urllib.request
from typing import Any

from lib import net

_id = [0]


def rpc(url: str, token: str | None, method: str, params: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    """Send one JSON-RPC request to the Gateway; return the HTTP status and the decoded body."""
    _id[0] += 1
    body = {"jsonrpc": "2.0", "id": _id[0], "method": method, **({"params": params} if params is not None else {})}
    req = urllib.request.Request(  # noqa: S310  Gateway URL comes from the saved state
        url,
        json.dumps(body).encode(),
        {
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            **({"authorization": "Bearer " + token} if token else {}),
        },
    )
    try:
        with net.open_https(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {
                "raw": raw.decode(errors="replace")[:300],
                "www-authenticate": e.headers.get("www-authenticate", "")[:300],
            }


def tool(url: str, token: str | None, name: str, args: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Call a Gateway tool through MCP tools/call; return the HTTP status and the decoded body."""
    s, r = rpc(url, token, "tools/call", {"name": name, "arguments": args})
    return s, r
