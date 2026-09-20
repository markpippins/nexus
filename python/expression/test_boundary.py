import unittest

from expression.boundary import validate_boundary
from expression.pipeline import build_expression_bundle


class ExpressionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.bundle = build_expression_bundle({
            "transcript_id": "boundary-001",
            "turns": [{"role": "user", "content": "Review PR #251."}],
        })

    def test_default_boundary_converges_with_existing_pipeline(self):
        boundary = self.bundle["boundary"]
        self.assertEqual(boundary["harvest_compatibility"], "converge")
        self.assertEqual(boundary["observation_storage"], "staging")
        self.assertEqual(boundary["canonical_identity_store"], "resolution")
        self.assertEqual(validate_boundary(self.bundle), [])

    def test_canonical_observation_storage_is_rejected(self):
        invalid = {**self.bundle, "boundary": {
            **self.bundle["boundary"],
            "observation_storage": "canonical",
        }}
        errors = validate_boundary(invalid)
        self.assertIn("Expression observations must remain staging material", errors)

    def test_expression_cannot_claim_authority(self):
        invalid = {**self.bundle, "authority_status": "authoritative", "boundary": {
            **self.bundle["boundary"],
            "authority_status": "authoritative",
        }}
        errors = validate_boundary(invalid)
        self.assertIn("Expression cannot claim authority", errors)

    def test_supersession_requires_record(self):
        invalid = {**self.bundle, "boundary": {
            **self.bundle["boundary"],
            "harvest_compatibility": "supersede",
        }}
        errors = validate_boundary(invalid)
        self.assertIn("supersession requires an explicit compatibility record", errors)


if __name__ == "__main__":
    unittest.main()
