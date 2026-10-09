import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from web_app.backend import server


class ExternalTolerancePredictionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.model_id = "sample_batch"
        self.part_id = "Part_1"
        os.makedirs(os.path.join(self.temp_dir.name, self.model_id), exist_ok=True)
        self.output_patch = patch.object(server, "OUTPUT_DIR", self.temp_dir.name)
        self.output_patch.start()
        self.rules_patch = patch.object(
            server,
            "get_candidate_annotation_rules",
            return_value={"rules": [{"rule_id": "rule-001"}]},
        )
        self.rules_patch.start()
        self.payload = {
            "schema_version": "1.0",
            "provider": "example-provider",
            "model_name": "tolerance-net",
            "model_version": "2026.09.1",
            "model_artifact_id": "sha256:example",
            "request_id": "request-001",
            "training_data_scope": "approved internal dataset v3",
            "predictions": [
                {
                    "rule_id": "rule-001",
                    "feature_id": "feature-001",
                    "predicted_mode": "CUSTOM_SYMMETRIC",
                    "tolerance_config": {"mode": "CUSTOM_SYMMETRIC", "dev": 0.02},
                    "formatted_display": "±0.02 mm",
                    "confidence": 0.87,
                    "explanation": ["Nominal diameter is inside the validated range."],
                    "input_features": {"diameter_mm": 12.0, "feature_type": "shaft_segment"},
                    "uncertainty": {"method": "ensemble", "stddev_mm": 0.004},
                    "warnings": [],
                }
            ],
            "metadata": {"runtime": "onnxruntime"},
        }

    def tearDown(self):
        self.rules_patch.stop()
        self.output_patch.stop()
        self.temp_dir.cleanup()

    def test_prediction_batch_round_trip_and_delete(self):
        prediction_set = server.ExternalTolerancePredictionSet(**self.payload)
        post_body = server.save_external_tolerance_predictions(
            self.model_id, self.part_id, prediction_set
        )
        self.assertEqual(post_body["prediction_count"], 1)

        body = server.get_external_tolerance_predictions(self.model_id, self.part_id)
        self.assertEqual(body["source_type"], "EXTERNAL_NEURAL_MODEL")
        self.assertEqual(body["predictions_by_rule"]["rule-001"]["confidence"], 0.87)
        self.assertIn("received_at_utc", body)

        delete_body = server.delete_external_tolerance_predictions(self.model_id, self.part_id)
        self.assertEqual(delete_body["status"], "ok")
        with self.assertRaises(HTTPException) as missing:
            server.get_external_tolerance_predictions(self.model_id, self.part_id)
        self.assertEqual(missing.exception.status_code, 404)

    def test_duplicate_rule_ids_are_rejected(self):
        self.payload["predictions"].append(dict(self.payload["predictions"][0]))
        prediction_set = server.ExternalTolerancePredictionSet(**self.payload)
        with self.assertRaises(HTTPException) as duplicate:
            server.save_external_tolerance_predictions(
                self.model_id, self.part_id, prediction_set
            )
        self.assertEqual(duplicate.exception.status_code, 400)
        self.assertIn("Duplicate rule_id", duplicate.exception.detail)

    def test_confidence_outside_unit_interval_is_rejected(self):
        self.payload["predictions"][0]["confidence"] = 1.2
        with self.assertRaises(ValidationError):
            server.ExternalTolerancePredictionSet(**self.payload)

    def test_mode_and_config_must_be_consistent(self):
        prediction = self.payload["predictions"][0]
        prediction["predicted_mode"] = "FIT"
        prediction["tolerance_config"] = {"mode": "CUSTOM_SYMMETRIC", "dev": 0.02}
        with self.assertRaises(ValidationError):
            server.ExternalTolerancePredictionSet(**self.payload)

    def test_unknown_rule_ids_are_rejected_in_strict_mode(self):
        prediction_set = server.ExternalTolerancePredictionSet(**self.payload)
        with patch.object(
            server,
            "get_candidate_annotation_rules",
            return_value={"rules": [{"rule_id": "different-rule"}]},
        ):
            with self.assertRaises(HTTPException) as unknown:
                server.save_external_tolerance_predictions(
                    self.model_id, self.part_id, prediction_set
                )
        self.assertEqual(unknown.exception.status_code, 422)
        self.assertEqual(unknown.exception.detail["unknown_rule_ids"], ["rule-001"])

    def test_configured_api_key_is_required(self):
        prediction_set = server.ExternalTolerancePredictionSet(**self.payload)
        with patch.dict(os.environ, {"CAD_EXTERNAL_PREDICTION_API_KEY": "secret"}):
            with self.assertRaises(HTTPException) as unauthorized:
                server.save_external_tolerance_predictions(
                    self.model_id,
                    self.part_id,
                    prediction_set,
                    x_api_key="wrong",
                )
        self.assertEqual(unauthorized.exception.status_code, 401)

    def test_boolean_deviation_is_rejected(self):
        prediction = self.payload["predictions"][0]
        prediction["tolerance_config"]["dev"] = True
        with self.assertRaises(ValidationError):
            server.ExternalTolerancePredictionSet(**self.payload)


if __name__ == "__main__":
    unittest.main()
