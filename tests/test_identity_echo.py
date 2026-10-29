"""The function presents identity claims from the managed authorizer event."""

import contextlib
import importlib.util
import io
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_handler():
    spec = importlib.util.spec_from_file_location("identity_echo", ROOT / "resource-api/handler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.handler


class IdentityEchoTest(unittest.TestCase):
    def test_returns_all_authorizer_claims_unchanged(self):
        claims = {
            "sub": "opaque-subject",
            "scope": "resource.read",
            "aud": "resource-api",
            "custom": "unchanged",
            "example_list": ["value"],
        }
        event = {
            "requestContext": {"authorizer": {"jwt": {"claims": claims}}},
            "headers": {"authorization": "not-a-token", "x-user-id": "forged"},
            "body": '{"sub":"request-body-subject","scope":"request-body-scope"}',
            "rawPath": "/v1/me",
        }
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            response = load_handler()(event, None)
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(json.loads(response["body"]), claims)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(event["requestContext"]["authorizer"]["jwt"]["claims"], claims)

    def test_requires_managed_authorizer_event_shape(self):
        with self.assertRaises(KeyError):
            load_handler()({"headers": {"authorization": "Bearer forged"}}, None)

    def test_echoes_minimal_authorizer_claims(self):
        claims = {"sub": "service-subject", "scope": "resource.read"}
        result = load_handler()({"requestContext": {"authorizer": {"jwt": {"claims": claims}}}}, None)
        self.assertEqual(json.loads(result["body"]), claims)
