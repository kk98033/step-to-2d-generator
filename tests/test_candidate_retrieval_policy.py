import sys
import unittest
from pathlib import Path


SCRIPT_ROOT = (
    Path(__file__).resolve().parents[1]
    / "experimental_lab"
    / "tolerance_extraction_audit"
    / "common"
    / "scripts"
)
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from merge_blade_all_tolerance_results import _apply_retrieval_policy


class CandidateRetrievalPolicyTests(unittest.TestCase):
    def test_verified_is_trusted_and_does_not_require_review(self):
        row = {"status": "VERIFIED", "semantic_path": "shaft.outer_diameter"}
        _apply_retrieval_policy(row)
        self.assertTrue(row["recommendation_eligible"])
        self.assertEqual(row["recommendation_label"], "TRUSTED")
        self.assertFalse(row["engineer_review_required"])

    def test_candidate_is_searchable_but_requires_review(self):
        row = {"status": "CANDIDATE_ONLY", "semantic_path": "repeated_radial_pattern.sectional_outer_envelope_diameter"}
        _apply_retrieval_policy(row)
        self.assertTrue(row["recommendation_eligible"])
        self.assertEqual(row["recommendation_label"], "REVIEW_REQUIRED")
        self.assertEqual(row["case_retrieval"]["confidence_label"], "MEDIUM")
        self.assertTrue(row["engineer_review_required"])

    def test_unresolved_is_not_used_for_feature_retrieval(self):
        row = {"status": "UNRESOLVED"}
        _apply_retrieval_policy(row)
        self.assertFalse(row["recommendation_eligible"])
        self.assertEqual(row["recommendation_label"], "EXCLUDED_UNRESOLVED")


if __name__ == "__main__":
    unittest.main()
