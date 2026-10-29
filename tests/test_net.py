"""Regression test for lib.net.open_https: https only, and no redirect ever carries a credential away."""

import pathlib
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib import net

AUTH_HEADER = "Bearer synthetic-test-value"


def _self_signed(directory: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """Write a throw-away certificate for localhost and return the certificate and key paths."""
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
            "-keyout",
            str(key_path),
            "-out",
            str(cert_path),
        ],
        check=True,
        capture_output=True,
    )
    key_path.chmod(0o600)
    return cert_path, key_path


class OpenHttpsTest(unittest.TestCase):
    """open_https refuses non-https URLs and redirects."""

    @classmethod
    def setUpClass(cls) -> None:
        """Start a plain-HTTP sink and a TLS server that redirects to it (and one that answers 200)."""
        cls.tmp = tempfile.TemporaryDirectory()
        cert, key = _self_signed(pathlib.Path(cls.tmp.name))
        cls.received: list[bool] = []

        class Sink(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                cls.received.append(self.headers.get("Authorization") == AUTH_HEADER)
                self.send_response(200)
                self.end_headers()

            do_GET = do_POST  # noqa: N815

            def log_message(self, *args: object) -> None:
                pass

        cls.sink = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
        sink_port = cls.sink.server_port

        class Tls(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                if self.path == "/redirect":
                    self.send_response(302)  # urllib follows a 302 to a POST by re-sending as GET, headers included
                    self.send_header("Location", f"http://127.0.0.1:{sink_port}/sink")
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", "2")
                self.end_headers()
                if self.path != "/redirect":
                    self.wfile.write(b"{}")

            do_GET = do_POST  # noqa: N815

            def log_message(self, *args: object) -> None:
                pass

        cls.tls = ThreadingHTTPServer(("127.0.0.1", 0), Tls)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        cls.tls.socket = context.wrap_socket(cls.tls.socket, server_side=True)
        for server in (cls.sink, cls.tls):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.client_context = ssl.create_default_context(cafile=str(cert))

    @classmethod
    def tearDownClass(cls) -> None:
        """Stop the servers."""
        for server in (cls.sink, cls.tls):
            server.shutdown()
            server.server_close()
        cls.tmp.cleanup()

    def _request(self, path: str) -> urllib.request.Request:
        return urllib.request.Request(
            f"https://localhost:{self.tls.server_port}{path}",
            data=b"{}",
            headers={"Authorization": AUTH_HEADER, "Content-Type": "application/json"},
        )

    def test_https_answer_passes(self) -> None:
        """A direct https response is returned."""
        with net.open_https(self._request("/ok"), timeout=5, context=self.client_context) as response:
            self.assertEqual(response.status, 200)

    def test_https_to_http_redirect_is_refused_and_nothing_is_forwarded(self) -> None:
        """A redirect to plain HTTP raises, and the plain-HTTP server never sees the Authorization header."""
        self.received.clear()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            net.open_https(self._request("/redirect"), timeout=5, context=self.client_context)
        self.assertEqual(caught.exception.code, 302)
        self.assertEqual(self.received, [])

    def test_non_https_urls_are_refused(self) -> None:
        """http, file and ftp URLs raise ValueError before any connection."""
        for url in ("http://localhost:1/", "file:///etc/hostname", "ftp://localhost/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                net.open_https(url, timeout=1)


if __name__ == "__main__":
    unittest.main()
