"""Present identity claims supplied by the API Gateway JWT authorizer."""

import json
from typing import Any


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Return the managed authorizer's claims as JSON with caching disabled."""
    claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(claims),
    }
