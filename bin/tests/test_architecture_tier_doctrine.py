"""Guard the tier doctrine in ARCHITECTURE.md against silent rot.

ARCHITECTURE.md §8 states that ports add implementations and never remove one, and that
liveness is opt-out. A future edit that quietly reintroduces delete-on-port or "deprecated"
as the default is a real regression -- it is how a redundant implementation gets deleted by
an agent correctly following the docs. These assertions are cheap and fail loudly.
"""
import pathlib
import re
import unittest

ARCH = pathlib.Path(__file__).resolve().parents[2] / "ARCHITECTURE.md"


class TestTierDoctrine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = ARCH.read_text(encoding="utf-8")

    def test_architecture_exists(self):
        self.assertTrue(ARCH.exists(), f"missing {ARCH}")

    def test_tier_section_present(self):
        for heading in ("### 8.1 Implementation Tiers", "### 8.2 Two independent axes",
                        "### 8.3 Ports find holes", "### 8.4 Contract provenance"):
            self.assertIn(heading, self.text, f"doctrine heading removed: {heading}")

    def test_two_axes_declared_with_liveness_default_live(self):
        # liveness must be opt-out: "live" is the stated default
        self.assertRegex(self.text, r"\*\*Liveness is opt-out\.\*\*")

    def test_host_affinity_is_not_lifecycle(self):
        self.assertIn("Host affinity is a placement, not a lifecycle", self.text)

    def test_port_does_not_retire_source(self):
        # the disposition rule: a port adds, never removes
        self.assertIn("Porting to a new tier **adds** an implementation", self.text)

    def test_zero_discrepancy_port_is_a_failure(self):
        self.assertIn("A port that surfaces zero discrepancies has failed", self.text)

    def test_contract_is_of_record_not_runtime(self):
        self.assertIn("the contract is the contract of record", self.text)

    def test_language_segment_is_provenance_not_canonicality(self):
        # typespec/v1/<service>/<language>/ must not be read as "python is canonical"
        self.assertIn("provenance, not canonicality", self.text)

    def test_port_receipt_is_referenced_and_exists(self):
        self.assertIn("docs/PORT-RECEIPT.md", self.text)
        receipt = ARCH.parent / "docs" / "PORT-RECEIPT.md"
        self.assertTrue(receipt.exists(), "docs/PORT-RECEIPT.md missing but referenced")

    def test_receipt_requires_independence_and_falsification(self):
        receipt = (ARCH.parent / "docs" / "PORT-RECEIPT.md").read_text(encoding="utf-8")
        self.assertIn("Independence argument", receipt)
        self.assertIn("Falsification count", receipt)
        # disposition of the source implementation is mandatory
        self.assertIn("Disposition of the source implementation", receipt)
        self.assertIn("Host affinity of X on this host", receipt)

    def test_receipt_requires_tester_attestation(self):
        receipt = (ARCH.parent / "docs" / "PORT-RECEIPT.md").read_text(encoding="utf-8")
        self.assertIn("Tester's attestation recorded", receipt)
        self.assertIn("self-attestation is not sufficient", receipt)

    def test_no_tier_claims_unverifiable_paths(self):
        # Guard against naming a service that does not exist as an exemption example.
        # Every ``path`` cited in the exemption paragraph must resolve.
        block = re.search(r"\*\*`exempt` means required everywhere\.\*\*(.*?)\n\n", self.text,
                          re.DOTALL)
        self.assertIsNotNone(block, "exempt paragraph not found")
        for cited in re.findall(r"`([a-z0-9_./-]+\.(?:json|py|md|ts))`", block.group(1)):
            candidates = [ARCH.parent / cited, ARCH.parent / "bin" / cited]
            self.assertTrue(any(c.exists() for c in candidates),
                            f"exemption cites non-existent path: {cited}")


if __name__ == "__main__":
    unittest.main()
