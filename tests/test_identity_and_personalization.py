import tempfile
import unittest
from pathlib import Path

from web_app.backend.engineer_personalization import personalize_recommendations
from web_app.backend.identity_store import IdentityStore


class IdentityAndPersonalizationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        database_path = Path(self.tempdir.name) / "identity.db"
        self.store = IdentityStore(f"sqlite:///{database_path.as_posix()}")
        self.addCleanup(self.store.close)
        self.admin = self.store.authenticate("admin", "ForceconAdmin!2026")
        self.assertIsNotNone(self.admin)
        created = self.store.create_user({
            "username": "engineer-a",
            "display_name": "Engineer A",
            "email": "engineer-a@example.test",
            "role": "ENGINEER",
            "password": "Engineer-A-Password",
        }, self.admin.id)
        self.engineer = self.store.authenticate("engineer-a", "Engineer-A-Password")
        self.assertEqual(created["id"], self.engineer.id)

    def test_session_and_model_ownership_are_isolated(self):
        token, _ = self.store.create_session(self.engineer.id)
        self.assertEqual(self.store.user_for_token(token).id, self.engineer.id)

        self.store.claim_model("model-a_batch", self.engineer.id, "model-a.step")
        self.assertTrue(self.store.can_access_model(self.engineer, "model-a_batch"))
        self.assertTrue(self.store.can_access_model(self.admin, "model-a_batch"))

        other = self.store.create_user({
            "username": "engineer-b",
            "display_name": "Engineer B",
            "role": "ENGINEER",
            "password": "Engineer-B-Password",
        }, self.admin.id)
        other_user = self.store.authenticate("engineer-b", "Engineer-B-Password")
        self.assertFalse(self.store.can_access_model(other_user, "model-a_batch"))
        self.assertEqual(self.store.list_artifacts(other_user), [])

    def test_annotation_creates_private_tolerance_and_placement_case(self):
        self.store.claim_model("model-a_batch", self.engineer.id)
        result = self.store.record_artifact_and_cases(
            user=self.engineer,
            model_id="model-a_batch",
            part_id="shaft-01",
            title="Shaft annotation",
            part_type="SHAFT",
            product_family="AL0W",
            output_files={"pdf_url": "/api/files/model-a_batch/private.pdf"},
            feature_records=[{
                "rule_id": "shaft_01",
                "category": "shaft_segment",
                "inferred_role": "BEARING_JOURNAL",
                "nominal_value": 35.0,
                "tolerance_config": {
                    "mode": "CUSTOM_LIMITS",
                    "upper_dev": 0.02,
                    "lower_dev": -0.01,
                },
                "preferred_view": "front",
                "side": "BOTTOM",
                "baseline": "LEFT",
                "offset": 12.0,
            }],
        )
        self.assertEqual(result["learned_case_count"], 1)
        cases = self.store.personal_cases(self.engineer.id)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["placement"]["preferred_view"], "front")
        self.assertEqual(cases[0]["tolerance_config"]["upper_dev"], 0.02)

        artifacts = self.store.list_artifacts(self.engineer)
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["output_files"]["pdf_url"], "/api/files/model-a_batch/private.pdf")
        self.assertFalse(self.store.delete_personal_case(self.engineer.id, "missing-case"))
        self.assertTrue(self.store.delete_personal_case(self.engineer.id, cases[0]["id"]))
        self.assertEqual(self.store.personal_cases(self.engineer.id), [])

    def test_preferences_can_be_reset_without_affecting_other_users(self):
        self.store.update_preferences(self.engineer.id, {
            "recommendation_mode": "PERSONAL_FIRST",
            "personal_case_weight": 0.8,
            "dimension_placement": {"preferred_view": "right", "offset": 22},
        })
        reset = self.store.reset_preferences(self.engineer.id)
        self.assertEqual(reset["recommendation_mode"], "BALANCED")
        self.assertEqual(reset["personal_case_weight"], 0.35)
        self.assertEqual(reset["dimension_placement"], {})

    def test_personal_first_adopts_only_the_same_engineers_case(self):
        self.store.update_preferences(self.engineer.id, {
            "recommendation_mode": "PERSONAL_FIRST",
            "personal_case_weight": 0.5,
            "personal_case_min_similarity": 0.80,
            "dimension_placement": {"preferred_view": "front"},
        })
        self.store.record_artifact_and_cases(
            user=self.engineer,
            model_id="model-a_batch",
            part_id="shaft-01",
            title="Shaft annotation",
            part_type="SHAFT",
            product_family="AL0W",
            output_files={"pdf_url": "/private.pdf", "svg_url": "/private.svg"},
            feature_records=[{
                "rule_id": "shaft_01",
                "category": "shaft_segment",
                "inferred_role": "BEARING_JOURNAL",
                "nominal_value": 35.0,
                "tolerance_config": {
                    "mode": "CUSTOM_LIMITS",
                    "upper_dev": 0.02,
                    "lower_dev": -0.01,
                },
                "preferred_view": "front",
                "side": "BOTTOM",
                "baseline": "LEFT",
            }],
        )
        result = personalize_recommendations(
            self.store,
            self.engineer,
            [{
                "rule_id": "shaft_01",
                "category": "shaft_segment",
                "inferred_role": "BEARING_JOURNAL",
                "nominal_value": 35.0,
            }],
            {"recommendations": {"shaft_01": {
                "rule_id": "shaft_01",
                "recommended_mode": "NONE",
                "evidence_cases": [],
            }}},
            "SHAFT",
            "AL0W",
        )
        recommendation = result["recommendations"]["shaft_01"]
        self.assertEqual(recommendation["tier_level"], "TIER_0_ENGINEER_PREFERENCE")
        self.assertEqual(recommendation["upper_dev"], 0.02)
        self.assertEqual(recommendation["engineer_placement_recommendation"]["side"], "BOTTOM")
        self.assertTrue(recommendation["personalization"]["adopted"])

        other = self.store.create_user({
            "username": "engineer-c",
            "display_name": "Engineer C",
            "role": "ENGINEER",
            "password": "Engineer-C-Password",
        }, self.admin.id)
        other_user = self.store.authenticate("engineer-c", "Engineer-C-Password")
        other_result = personalize_recommendations(
            self.store,
            other_user,
            [{"rule_id": "shaft_01", "category": "shaft_segment", "nominal_value": 35.0}],
            {"recommendations": {"shaft_01": {"rule_id": "shaft_01", "evidence_cases": []}}},
            "SHAFT",
            "AL0W",
        )
        self.assertEqual(other_result["engineer_personalization"]["personal_case_count"], 0)


if __name__ == "__main__":
    unittest.main()
