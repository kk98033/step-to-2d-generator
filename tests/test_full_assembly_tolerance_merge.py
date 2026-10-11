import unittest
from pathlib import Path

from experimental_lab.tolerance_extraction_audit.common.scripts.merge_full_assembly_tolerance_results import (
    normalize_component_row,
)


class FullAssemblyToleranceMergeTests(unittest.TestCase):
    def test_semantic_recovery_is_a_trusted_link(self):
        row = normalize_component_row(
            "PART-A",
            {"status": "SEMANTICALLY_RECOVERED", "feature_id": "shaft", "nominal_field": "diameter"},
            Path("report.json"),
        )
        self.assertEqual(row["status"], "VERIFIED")
        self.assertEqual(row["recommendation_label"], "TRUSTED")
        self.assertFalse(row["engineer_review_required"])

    def test_candidate_remains_searchable_with_review_label(self):
        row = normalize_component_row(
            "PART-B",
            {"status": "CANDIDATE_ONLY", "feature_id": "edge", "nominal_field": "length"},
            Path("report.json"),
        )
        self.assertTrue(row["recommendation_eligible"])
        self.assertEqual(row["recommendation_label"], "REVIEW_REQUIRED")
        self.assertTrue(row["engineer_review_required"])

    def test_unresolved_is_excluded(self):
        row = normalize_component_row("PART-C", {"status": "UNRESOLVED"}, Path("report.json"))
        self.assertFalse(row["recommendation_eligible"])
        self.assertEqual(row["recommendation_label"], "EXCLUDED_UNRESOLVED")


if __name__ == "__main__":
    unittest.main()
