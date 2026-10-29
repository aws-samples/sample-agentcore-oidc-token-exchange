"""HTTPS-only URL opening shared by the scripts."""

import ssl
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPResponse
from typing import Any


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect, so a credential header is never forwarded to another URL or over plain HTTP."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        """Return None: urllib then raises HTTPError for the 3xx response instead of following it."""
        return None


_OPENER = urllib.request.build_opener(_RefuseRedirects)


def open_https(
    request: urllib.request.Request | str, timeout: float, context: ssl.SSLContext | None = None
) -> HTTPResponse:
    """Open `request` with urllib, refusing every scheme except https and every redirect.

    Every URL the sample opens (IdP endpoints, the Gateway, Keycloak) is an https URL that answers directly. Checking
    the scheme keeps urllib from opening a ``file:`` or custom-scheme URL if a configuration value is ever wrong, and
    refusing redirects keeps the Authorization header from following a redirect to another host or to plain HTTP.
    `context` is an optional TLS context (for example one that trusts a private CA); the default trusts the system CAs.
    A redirect surfaces as ``urllib.error.HTTPError`` (status 3xx), which the callers already handle.
    """
    url = request if isinstance(request, str) else request.full_url
    if urllib.parse.urlparse(url).scheme != "https":
        raise ValueError(f"refusing to open a non-https URL: {url.split('?')[0]}")
    # The scheme was checked above and redirects are refused by the opener.
    opener = (
        _OPENER
        if context is None
        else urllib.request.build_opener(_RefuseRedirects, urllib.request.HTTPSHandler(context=context))
    )
    return opener.open(request, timeout=timeout)  # noqa: S310  # nosec B310
