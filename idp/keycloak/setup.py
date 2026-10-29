#!/usr/bin/env python3
"""Create the Keycloak realm, clients, scopes and test users (idempotent).

Realm KC_REALM (default `sample`) in the Keycloak created by infra.py. Needs the admin console reachable from this host.

clients   app           confidential; the enterprise application (password grant enabled for the tests,
                        no service account)
          gateway       confidential; the Gateway's inbound audience AND the token-exchange (RFC 8693) middle tier
          resource-api  the downstream API: an audience only (no flows)
scopes    gateway-access (aud=gateway), resource.read (aud=resource-api)
users     <p>-user-a, <p>-user-b; `sub` is the user's stable UUID
Secrets   <p>/keycloak/app-client, gateway-client, users (Secrets Manager, SECRETS_REGION); nothing secret is printed.
Access tokens live 5 minutes. Writes the non-secret ids to .state/keycloak-state.json.
"""

import json
import pathlib
import secrets
import string
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from lib import idp, net

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import adapter

P, REALM, HOST = idp.PREFIX, adapter.REALM, "https://" + adapter.host()
TAGS = [{"Key": "project", "Value": P}]


def call(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    token: str | None = None,
    form: dict[str, str] | None = None,
    ok: tuple[int, ...] = (200, 201, 204),
) -> tuple[int, Any]:
    """Send a request to Keycloak; return the status and parsed JSON, exit on an HTTP error not in `ok`."""
    data = urllib.parse.urlencode(form).encode() if form else (json.dumps(body).encode() if body is not None else None)
    h = {"content-type": "application/x-www-form-urlencoded" if form else "application/json"}
    if token:
        h["authorization"] = "Bearer " + token
    try:
        req = urllib.request.Request(HOST + path, data, h, method=method)  # noqa: S310  scheme checked by open_https
        with net.open_https(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        if e.code in ok:
            return e.code, {}
        raise SystemExit(f"{method} {path} -> {e.code} {raw[:300]!r}") from None


tok = adapter._admin_token()


def admin(m: str, p: str, b: dict[str, Any] | None = None, **k: Any) -> tuple[int, Any]:
    """Call the realm admin API with the admin token."""
    return call(m, "/admin/realms" + p, b, tok, **k)


if not any(r["realm"] == REALM for r in admin("GET", "")[1]):
    admin(
        "POST",
        "",
        {
            "realm": REALM,
            "enabled": True,
            "accessTokenLifespan": 300,
            "registrationAllowed": False,
            "loginWithEmailAllowed": True,
            "sslRequired": "external",
        },
    )


def realm_path(p: str = "") -> str:
    """Return the admin API path below the realm."""
    return "/" + REALM + p


def find(path: str, key: str, value: str) -> dict[str, Any] | None:
    """Return the first admin API object at `path` whose `key` equals `value`, or None."""
    return next((x for x in admin("GET", realm_path(path))[1] if x.get(key) == value), None)


def scope(name: str, mappers: list[dict[str, Any]]) -> str:
    """Create the client scope with its mappers if missing; return its id."""
    s = find("/client-scopes", "name", name)
    if not s:
        admin(
            "POST",
            realm_path("/client-scopes"),
            {
                "name": name,
                "protocol": "openid-connect",
                "attributes": {"include.in.token.scope": "true", "display.on.consent.screen": "false"},
            },
        )
        s = find("/client-scopes", "name", name)
    have = {m["name"] for m in s.get("protocolMappers", [])}
    for m in mappers:
        if m["name"] not in have:
            admin("POST", realm_path(f"/client-scopes/{s['id']}/protocol-mappers/models"), m)
    return s["id"]


def aud(client: str) -> dict[str, Any]:
    """Return an audience mapper that adds `client` to the access token."""
    return {
        "name": f"aud-{client}",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-audience-mapper",
        "config": {"included.client.audience": client, "id.token.claim": "false", "access.token.claim": "true"},
    }


scopes = {
    "gateway-access": scope("gateway-access", [aud("gateway")]),
    "resource.read": scope("resource.read", [aud("resource-api")]),
    "resource.audience": scope("resource.audience", [aud("resource-api")]),
}


def client(cid: str, body: dict[str, Any]) -> str:
    """Create or update the client and attach the scopes; return its internal id."""
    c = find("/clients", "clientId", cid)
    if c:
        admin("PUT", realm_path(f"/clients/{c['id']}"), {**c, **body})
    else:
        admin("POST", realm_path("/clients"), {"clientId": cid, **body})
    c = find("/clients", "clientId", cid)
    for sid in scopes.values():
        admin(
            "PUT",
            realm_path(f"/clients/{c['id']}/optional-client-scopes/{sid}"),
        )
    return c["id"]


base = {
    "enabled": True,
    "protocol": "openid-connect",
    "publicClient": False,
    "clientAuthenticatorType": "client-secret",
}
ids = {
    "resource-api": client(
        "resource-api",
        {**base, "standardFlowEnabled": False, "directAccessGrantsEnabled": False, "serviceAccountsEnabled": False},
    ),
    "gateway": client(
        "gateway",
        {
            **base,
            "standardFlowEnabled": False,
            "directAccessGrantsEnabled": True,
            "serviceAccountsEnabled": True,
            "attributes": {"standard.token.exchange.enabled": "true", "access.token.lifespan": "300"},
        },
    ),
    "app": client(
        "app",
        {
            **base,
            "standardFlowEnabled": True,
            "directAccessGrantsEnabled": True,
            "serviceAccountsEnabled": False,
            "redirectUris": ["http://localhost:8400/callback"],
            "webOrigins": ["http://localhost:8400"],
        },
    ),
}
for name in ("app", "gateway"):
    sec = admin("GET", realm_path(f"/clients/{ids[name]}/client-secret"))[1]["value"]
    idp.put_secret(
        idp.secret_id("keycloak", f"{name}-client"),
        {"issuer": adapter.issuer(), "client_id": name, "client_secret": sec},
        TAGS,
    )

try:
    pw = idp.secret(idp.secret_id("keycloak", "users"))
except Exception:
    pw = {}
uids = {}
for u in idp.USERS:
    name = f"{P}-{u}"
    if not find("/users?username=" + name, "username", name):
        pw[name] = {
            "username": name,
            "password": "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(28)),
        }
        admin(
            "POST",
            realm_path("/users"),
            {
                "username": name,
                "email": f"{name}@example.invalid",
                "emailVerified": True,
                "enabled": True,
                "firstName": name,
                "lastName": "Sample",
                "credentials": [{"type": "password", "value": pw[name]["password"], "temporary": False}],
            },
        )
    uids[name] = find("/users?username=" + name, "username", name)["id"]
idp.put_secret(idp.secret_id("keycloak", "users"), pw, TAGS)
json.dump(
    {"issuer": adapter.issuer(), "clients": ids, "users": uids},
    open(idp.state_file("keycloak", "state"), "w", encoding="utf-8"),
    indent=1,
)
print("ok", adapter.issuer())
