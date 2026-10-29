"""Elastic IP selection of the Keycloak instance: named, untagged (project tag only) and ambiguity."""

import os
import pathlib
import sys
import unittest

os.environ.setdefault("SAMPLE_PREFIX", "sample-oidc")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib import idp

NAME = f"{idp.PREFIX}-kc"
TAGS = [{"Key": "project", "Value": idp.PREFIX}]


def address(n: int, name: str | None = None) -> dict:
    """Return a describe_addresses entry tagged project=<prefix> and, if given, Name=`name`."""
    tags = [*TAGS, *([{"Key": "Name", "Value": name}] if name else [])]
    return {"AllocationId": f"eipalloc-{n}", "PublicIp": f"192.0.2.{n}", "Tags": tags}


class FakeEc2:
    """Records the calls that change state and answers describe_addresses with the given entries."""

    def __init__(self, *addresses: dict) -> None:
        """Keep the entries returned by describe_addresses."""
        self.addresses, self.calls = list(addresses), []

    def describe_addresses(self, Filters: list) -> dict:  # noqa: N803  boto3 signature
        """Return the entries, as the project tag filter would."""
        return {"Addresses": self.addresses}

    def create_tags(self, **kw: object) -> None:
        """Record an adoption."""
        self.calls.append(("create_tags", kw["Resources"], kw["Tags"]))

    def allocate_address(self, **kw: object) -> dict:
        """Record an allocation and return a new address."""
        self.calls.append(("allocate_address",))
        return {"AllocationId": "eipalloc-new", "PublicIp": "192.0.2.99"}


class KeycloakEipTest(unittest.TestCase):
    """A project-tagged EIP without a Name tag is found, adopted and cleaned up."""

    def test_untagged_eip_is_adopted_not_replaced(self) -> None:
        """A single project-tagged EIP without Name gets the Name tag and no new IP is allocated."""
        ec2 = FakeEc2(address(1))
        eip = idp.claim_keycloak_eip(ec2, NAME, TAGS)
        self.assertEqual(eip["AllocationId"], "eipalloc-1")
        self.assertEqual(ec2.calls, [("create_tags", ["eipalloc-1"], [{"Key": "Name", "Value": NAME}])])

    def test_named_eip_is_reused_unchanged(self) -> None:
        """An EIP that already has Name=<prefix>-kc is returned without any change."""
        ec2 = FakeEc2(address(1, NAME))
        self.assertEqual(idp.claim_keycloak_eip(ec2, NAME, TAGS)["AllocationId"], "eipalloc-1")
        self.assertEqual(ec2.calls, [])

    def test_no_eip_allocates_one(self) -> None:
        """Without candidates a new EIP is allocated (and tagged by the allocation)."""
        ec2 = FakeEc2(address(1, "someone-elses-name"))
        self.assertEqual(idp.claim_keycloak_eip(ec2, NAME, TAGS)["AllocationId"], "eipalloc-new")
        self.assertEqual(ec2.calls, [("allocate_address",)])

    def test_ambiguous_candidates_are_refused_before_any_change(self) -> None:
        """Two candidates (any mix of forms) stop the run with a clear error and no tag or allocation."""
        for addresses in ((address(1), address(2)), (address(1, NAME), address(2))):
            with self.subTest(n=len(addresses)):
                ec2 = FakeEc2(*addresses)
                with self.assertRaises(SystemExit) as caught:
                    idp.claim_keycloak_eip(ec2, NAME, TAGS)
                self.assertIn("Elastic IPs carry project=", str(caught.exception))
                self.assertEqual(ec2.calls, [])

    def test_cleanup_finds_both_forms_and_ignores_foreign_names(self) -> None:
        """keycloak_eips (used by cleanup) returns named and untagged EIPs but not ones with another Name."""
        named, untagged = idp.keycloak_eips(FakeEc2(address(1, NAME), address(2), address(3, "other")), NAME)
        self.assertEqual([a["AllocationId"] for a in named + untagged], ["eipalloc-1", "eipalloc-2"])


if __name__ == "__main__":
    unittest.main()
