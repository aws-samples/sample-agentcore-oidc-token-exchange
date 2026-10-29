"""IdP adapter for Keycloak 26.

Realm KC_REALM (default `sample`), on the AWS host created by infra.py.
Tokens are returned to callers, never printed.
"""

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from lib import idp, net

NAME, PROVIDER = "keycloak", f"{idp.PREFIX}-keycloak"
USER_ID_CLAIM = "sub"  # Keycloak's sub is the user's UUID: stable and identical for every client (not pairwise)
P = idp.PREFIX
REALM = os.environ.get("KC_REALM", "sample")
SCOPES = {"gateway": "gateway-access", "resource": "resource.read"}


def host() -> str:
    """Return the Keycloak host name (KC_HOSTNAME or the one recorded by infra.py)."""
    if os.environ.get("KC_HOSTNAME"):
        return os.environ["KC_HOSTNAME"]
    return json.load(open(idp.state_file(NAME, "infra"), encoding="utf-8"))["host"]


def issuer() -> str:
    """Return the issuer URL of the realm."""
    return f"https://{host()}/realms/{REALM}"


def state() -> dict[str, Any]:
    """Return the non-secret Keycloak state written by the setup script."""
    return json.load(open(idp.state_file(NAME, "state"), encoding="utf-8"))


def _client(name: str) -> dict[str, Any]:
    """Return the stored credentials of the named client."""
    return idp.secret(idp.secret_id(NAME, f"{name}-client"))


def login_raw(user: str, scope: str, client: str = "app") -> dict[str, Any]:
    """Resource-owner password grant. TEST ONLY: production flows use the authorization code flow with PKCE."""
    c, users = _client(client), idp.secret(idp.secret_id(NAME, "users"))
    return idp.post_form(
        issuer() + "/protocol/openid-connect/token",
        {
            "grant_type": "password",
            "client_id": c["client_id"],
            "client_secret": c["client_secret"],
            "username": f"{P}-{user}",
            "password": users[f"{P}-{user}"]["password"],
            "scope": scope,
        },
    )


def token(user: str, resource: str) -> str:
    """Return an access token for the user, issued for the given resource."""
    return login_raw(user, SCOPES[resource])["access_token"]


def id_token(user: str) -> str:
    """Return an ID token for the user."""
    return login_raw(user, "openid")["id_token"]


def token_without_resource_scope(user: str) -> str:
    """Return an audience-valid token without resource.read for the managed-gate test."""
    return login_raw(user, "resource.audience")["access_token"]


def other_resource_token(user: str) -> str:
    """A valid token for none of our resources (only Keycloak's default `account` audience)."""
    return login_raw(user, "openid")["access_token"]


def other_client_token(user: str) -> str:
    """Return a token accepted by the gateway door but issued to another client (azp is not the application)."""
    return login_raw(user, SCOPES["gateway"], client="gateway")["access_token"]


def door() -> dict[str, Any]:
    """Return what the Gateway's inbound CUSTOM_JWT check is configured with."""
    return {
        "discovery_url": issuer() + "/.well-known/openid-configuration",
        "audiences": ["gateway"],
        "client_claim": "azp",
        "client_value": "app",
    }


def resource_api_config() -> dict[str, Any]:
    """Return configuration for the managed HTTP API JWT authorizer and route."""
    return {
        "issuer": issuer(),
        "audiences": ["resource-api"],
        "required_scope": "resource.read",
    }


def provider_input() -> tuple[str, dict[str, Any]]:
    """Identity OAuth2 provider: a custom provider with RFC 8693 token exchange (no actor token)."""
    gw = _client("gateway")
    return "CustomOauth2", {
        "customOauth2ProviderConfig": {
            "oauthDiscovery": {"discoveryUrl": issuer() + "/.well-known/openid-configuration"},
            "clientId": gw["client_id"],
            "clientSecret": gw["client_secret"],
            "clientAuthenticationMethod": "CLIENT_SECRET_BASIC",
            "onBehalfOfTokenExchangeConfig": {
                "grantType": "TOKEN_EXCHANGE",
                "tokenExchangeGrantTypeConfig": {"actorTokenContent": "NONE"},
            },
        }
    }


def exchange_params() -> dict[str, Any]:
    """Return the token exchange parameters used by the Gateway outbound auth.

    `audience` names the resource client and the scope selects resource access. AgentCore Identity sends the JWT with
    subject_token_type=...:jwt, which Keycloak's standard exchange refuses
    ("Parameter 'subject_token' supports access tokens only"); the override below makes it send ...:access_token.
    """
    return {
        "scopes": ["resource.read"],
        "grant": "TOKEN_EXCHANGE",
        "custom": {"audience": "resource-api", "subject_token_type": "urn:ietf:params:oauth:token-type:access_token"},  # nosec B105
    }


def expected_exchanged_azp() -> str:
    """Return the authorized party expected in the exchanged token."""
    return "gateway"


def expected_resource_aud() -> str:
    """Return the audience expected in the exchanged token."""
    return "resource-api"


def unconsented_scope() -> list[str]:
    """Return scopes whose audience is not the resource API (negative test)."""
    return ["gateway-access"]  # its audience is not the resource API: the exchange must not yield a resource token


def _admin_token() -> str:
    """Return an admin access token from the master realm."""
    adm = idp.secret(idp.secret_id(NAME, "admin"))
    return idp.post_form(
        f"https://{host()}/realms/master/protocol/openid-connect/token",
        {"grant_type": "password", "client_id": "admin-cli", "username": adm["username"], "password": adm["password"]},
    )["access_token"]


def admin_call(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    """Call the realm admin API; return the HTTP status and the parsed JSON body (empty on error)."""
    req = urllib.request.Request(
        f"https://{host()}/admin/realms/{REALM}" + path,
        json.dumps(body).encode() if body is not None else None,
        {"authorization": "Bearer " + _admin_token(), "content-type": "application/json"},
        method=method,
    )
    try:
        with net.open_https(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, {}
