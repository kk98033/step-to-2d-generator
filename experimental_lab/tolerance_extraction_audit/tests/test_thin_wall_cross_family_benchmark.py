import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.run_thin_wall_cross_family_benchmark import (
    _classification,
    _metrics,
)


class ThinWallCrossFamilyBenchmarkTests(unittest.TestCase):
    def test_classification_keeps_audit_cases_outside_truth_labels(self):
        self.assertEqual(_classification(True, True), "TRUE_POSITIVE")
        self.assertEqual(_classification(False, False), "TRUE_NEGATIVE")
        self.assertEqual(_classification(True, False), "FALSE_NEGATIVE")
        self.assertEqual(_classification(False, True), "FALSE_POSITIVE")
        self.assertEqual(_classification(None, True), "AUDIT_DETECTED")

    def test_metrics_do_not_count_audit_only_cases_as_accuracy(self):
        cases = [
            {"expected_detection": True, "classification": "TRUE_POSITIVE", "verification_status": "AUTO_VERIFIED", "elapsed_seconds": 1},
            {"expected_detection": False, "classification": "TRUE_NEGATIVE", "verification_status": None, "elapsed_seconds": 1},
            {"expected_detection": None, "classification": "AUDIT_DETECTED", "verification_status": "REVIEW_CANDIDATE", "elapsed_seconds": 1},
        ]
        metrics = _metrics(cases)
        self.assertEqual(metrics["labelled_case_count"], 2)
        self.assertEqual(metrics["audit_only_case_count"], 1)
        self.assertEqual(metrics["accuracy"], 1.0)
        self.assertEqual(metrics["audit_detected_count"], 1)


if __name__ == "__main__":
    unittest.main()
