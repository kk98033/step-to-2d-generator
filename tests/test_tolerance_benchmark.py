import tempfile
import unittest
from pathlib import Path

from auto_2d_drawing.tolerance.benchmark import (
    BenchmarkDataset,
    ToleranceBenchmarkRunner,
    assign_group_splits,
    audit_split_leakage,
    base_part_number,
    build_silver_dataset,
    tolerance_equal,
)
from auto_2d_drawing.tolerance.case_base import ToleranceCase
from auto_2d_drawing.tolerance.tolerance_decision_service import ToleranceDecisionService


def make_case(
    case_id,
    drawing,
    dev,
    status="AUTO_VERIFIED",
    diameter=10.0,
):
    return ToleranceCase(
        case_id=case_id,
        part_type="SHAFT",
        feature_type="shaft_segment",
        inferred_role="SHAFT_SEGMENT_DIAMETER",
        nominal_dimensions={"diameter": diameter, "length": 8.0},
        neighbor_types=["locating_shoulder"],
        boundary_position="INTERIOR",
        tolerance_config={"mode": "CUSTOM_LIMITS", "upper_dev": dev, "lower_dev": -dev},
        confidence=0.97,
        evidence_source=drawing,
        description="benchmark fixture",
        verification_status=status,
        source_metadata={
            "drawing_file": drawing,
            "entity_handle": case_id,
            "dimension_category": "DIAMETER",
            "matched_nominal_field": "diameter",
            "product_family": "TEST",
            "feature_identity_verified": status == "AUTO_VERIFIED",
        },
    )


class ToleranceBenchmarkTests(unittest.TestCase):
    def test_revision_suffix_is_removed_from_group_key(self):
        self.assertEqual(base_part_number("2FQ6V4030H-R00.dwg"), "2FQ6V4030H")
        self.assertEqual(base_part_number("2FQ6V4030H-R12.dxf"), "2FQ6V4030H")
        self.assertEqual(base_part_number("2FQ6V4030H-A03.dxf"), "2FQ6V4030H")

    def test_group_split_is_deterministic_and_leakage_free(self):
        groups = ["part:A", "part:A", "part:B", "part:C", "part:D"]
        first = assign_group_splits(groups)
        second = assign_group_splits(list(reversed(groups)))
        self.assertEqual(first, second)
        self.assertEqual(set(first.values()), {"train", "validation", "test"})

    def test_silver_dataset_excludes_unverified_cases(self):
        cases = [
            make_case("verified", "1TEST0001H-R01.dxf", 0.01),
            make_case("unverified", "1TEST0002H-R01.dxf", 0.02, status="AUTO_EXTRACTED"),
        ]
        dataset = build_silver_dataset(cases)
        self.assertEqual([item.expected["source_case_id"] for item in dataset.records], ["verified"])
        self.assertTrue(audit_split_leakage(dataset.records)["passed"])

    def test_dataset_round_trip(self):
        dataset = build_silver_dataset([make_case("verified", "1TEST0001H-R01.dxf", 0.01)])
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dataset.json"
            dataset.save(path)
            restored = BenchmarkDataset.load(path)
        self.assertEqual(restored.to_dict(), dataset.to_dict())

    def test_symmetric_and_explicit_limits_compare_equally(self):
        symmetric = {"mode": "CUSTOM_SYMMETRIC", "dev": 0.01}
        explicit = {"mode": "CUSTOM_SYMMETRIC", "upper_dev": 0.01, "lower_dev": -0.01}
        self.assertTrue(tolerance_equal(symmetric, explicit))

    def test_runner_excludes_all_revisions_of_query_part(self):
        cases = [
            make_case("same-r00", "1TEST0001H-R00.dxf", 0.01),
            make_case("same-r02", "1TEST0001H-R02.dxf", 0.01),
            make_case("other", "1TEST0002H-R00.dxf", 0.01),
        ]
        dataset = build_silver_dataset(cases)
        report = ToleranceBenchmarkRunner(cases, dataset).run(replay_sources=False)
        rows = {row["benchmark_id"]: row for row in report["records"]}
        for record in dataset.records:
            row = rows[record.benchmark_id]
            if record.group_id == "part:1TEST0001H":
                self.assertNotIn("same-r00", row["retrieved_case_ids"])
                self.assertNotIn("same-r02", row["retrieved_case_ids"])

    def test_consensus_requires_multiple_independent_parts(self):
        cases = [
            make_case("a-r00", "1TEST0001H-R00.dxf", 0.01),
            make_case("a-r01", "1TEST0001H-R01.dxf", 0.01),
            make_case("b-r00", "1TEST0002H-R00.dxf", 0.01),
        ]
        matches = [
            {"case": case, "similarity": 0.8 - index * 0.01}
            for index, case in enumerate(cases)
        ]
        summary, adopted = ToleranceDecisionService._summarize_case_consensus(matches)
        self.assertTrue(summary["eligible"])
        self.assertEqual(summary["support_part_count"], 2)
        self.assertIsNotNone(adopted)

    def test_conflicting_consensus_is_not_adopted(self):
        cases = [
            make_case("a", "1TEST0001H-R00.dxf", 0.01),
            make_case("b", "1TEST0002H-R00.dxf", 0.02),
            make_case("c", "1TEST0003H-R00.dxf", 0.03),
        ]
        matches = [{"case": case, "similarity": 0.9} for case in cases]
        summary, adopted = ToleranceDecisionService._summarize_case_consensus(matches)
        self.assertFalse(summary["eligible"])
        self.assertTrue(summary["near_top_conflict"])
        self.assertIsNone(adopted)


if __name__ == "__main__":
    unittest.main()
