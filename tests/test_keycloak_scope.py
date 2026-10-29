"""The negative scope fixture uses the same resource audience without the required scope."""

import unittest
from unittest import mock

from lib import idp


class KeycloakScopeTest(unittest.TestCase):
    def test_requests_resource_audience_only_scope(self):
        adapter = idp.load("keycloak")
        with mock.patch.object(adapter, "login_raw", return_value={"access_token": "fixture"}) as login:
            self.assertEqual(adapter.token_without_resource_scope("user-a"), "fixture")
        login.assert_called_once_with("user-a", "resource.audience")
