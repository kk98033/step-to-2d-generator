import tempfile
import unittest
from datetime import datetime
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
        self.assertFalse(self.admin.must_change_password)
        created = self.store.create_user({
            "username": "engineer-a",
            "display_name": "Engineer A",
            "email": "engineer-a@forcecon.com.tw",
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

    def test_deleted_artifact_and_cases_can_be_restored_from_trash(self):
        result = self.store.record_artifact_and_cases(
            user=self.engineer,
            model_id="recoverable-model",
            part_id="shaft-restore",
            output_files={"pdf_url": "/private/recoverable.pdf"},
            feature_records=[{
                "rule_id": "recoverable-rule",
                "category": "shaft_segment",
                "nominal_value": 20.0,
                "tolerance_config": {"mode": "SYMMETRIC", "value": 0.02},
            }],
        )
        self.assertTrue(self.store.delete_artifact(self.engineer, result["artifact_id"]))
        self.assertEqual(self.store.list_artifacts(self.engineer), [])
        self.assertEqual(self.store.personal_cases(self.engineer.id), [])

        trash = self.store.list_trash(self.engineer)
        self.assertEqual(len(trash), 1)
        self.assertEqual(trash[0]["record_type"], "ANNOTATION_ARTIFACT")
        self.store.set_trash_retention_days(60, self.admin.id)
        trash = self.store.list_trash(self.engineer)
        retention = datetime.fromisoformat(trash[0]["purge_after"]) - datetime.fromisoformat(trash[0]["deleted_at"])
        self.assertEqual(retention.days, 60)
        restored = self.store.restore_trash(self.engineer, trash[0]["id"])
        self.assertTrue(restored["restored"])
        self.assertEqual(len(self.store.list_artifacts(self.engineer)), 1)
        self.assertEqual(len(self.store.personal_cases(self.engineer.id)), 1)
        self.assertEqual(self.store.list_trash(self.engineer), [])

    def test_admin_can_restore_an_engineers_deleted_case(self):
        self.store.record_artifact_and_cases(
            user=self.engineer,
            model_id="recoverable-model",
            part_id="case-only",
            output_files={},
            feature_records=[{
                "rule_id": "case-only-rule",
                "category": "hole",
                "nominal_value": 8.0,
                "tolerance_config": {"mode": "SYMMETRIC", "value": 0.01},
            }],
        )
        case_id = self.store.personal_cases(self.engineer.id)[0]["id"]
        self.assertTrue(self.store.delete_personal_case(self.engineer.id, case_id))
        trash = self.store.list_trash(self.admin, self.engineer.id)
        self.assertEqual(len(trash), 1)
        self.store.restore_trash(self.admin, trash[0]["id"])
        self.assertEqual(len(self.store.personal_cases(self.engineer.id)), 1)

    def test_cases_can_be_partitioned_by_multiple_recommendation_tags(self):
        customer = self.store.create_recommendation_tag({
            "key": "customer:alpha",
            "name": "客戶 Alpha",
            "dimension": "CUSTOMER",
        }, self.admin.id)
        process = self.store.create_recommendation_tag({
            "key": "process:grinding",
            "name": "研磨",
            "dimension": "PROCESS",
        }, self.admin.id)
        self.store.record_artifact_and_cases(
            user=self.engineer,
            model_id="tagged-model",
            part_id="tagged-part",
            output_files={},
            tag_ids=[customer["id"], process["id"]],
            feature_records=[{
                "rule_id": "tagged-rule",
                "category": "shaft_segment",
                "nominal_value": 30.0,
                "tolerance_config": {"mode": "SYMMETRIC", "value": 0.01},
            }],
        )
        self.assertEqual(len(self.store.personal_cases(
            self.engineer.id, [customer["id"]], "ANY"
        )), 1)
        self.assertEqual(len(self.store.personal_cases(
            self.engineer.id, [customer["id"], process["id"]], "ALL"
        )), 1)
        unknown = "00000000-0000-0000-0000-000000000000"
        self.assertEqual(self.store.personal_cases(self.engineer.id, [unknown], "ANY"), [])

        self.store.assign_company_case_tags("COMPANY_CASE_1", [customer["id"]], self.admin.id)
        allowed = self.store.company_case_ids_for_tags([customer["id"]], "ANY")
        self.assertEqual(allowed, {"COMPANY_CASE_1"})

    def test_company_email_and_initial_password_lifecycle(self):
        created = self.store.create_user({
            "email": "new.engineer@forcecon.com.tw",
            "display_name": "New Engineer",
            "role": "ENGINEER",
        }, self.admin.id)
        self.assertTrue(created["initial_password"])
        user = self.store.authenticate(
            "new.engineer", created["initial_password"], "forcecon.com.tw"
        )
        self.assertIsNotNone(user)
        self.assertTrue(user.must_change_password)
        visible = next(item for item in self.store.list_users(True) if item["id"] == user.id)
        self.assertEqual(visible["initial_password"], created["initial_password"])

        self.store.change_password(user.id, created["initial_password"], "Changed-Password-2026!")
        hidden = next(item for item in self.store.list_users(True) if item["id"] == user.id)
        self.assertIsNone(hidden["initial_password"])
        self.assertIsNotNone(self.store.authenticate(
            "new.engineer", "Changed-Password-2026!", "forcecon.com.tw"
        ))

        reset_password = self.store.reset_password(user.id, None, self.admin.id)
        self.assertTrue(reset_password)
        reset_visible = next(item for item in self.store.list_users(True) if item["id"] == user.id)
        self.assertEqual(reset_visible["initial_password"], reset_password)
        notifications = self.store.pending_credential_notifications()
        self.assertTrue(any(item["user_id"] == user.id for item in notifications))

    def test_bulk_accounts_reject_non_company_domains(self):
        with self.assertRaises(ValueError):
            self.store.bulk_create_users([{
                "email": "outsider@example.com",
                "display_name": "Outsider",
            }], self.admin.id)
        created = self.store.bulk_create_users([{
            "email": "bulk.one@forcecon.com.tw",
            "display_name": "Bulk One",
        }, {
            "email": "bulk.two@forcecon.com.tw",
            "display_name": "Bulk Two",
            "role": "ENGINEER",
        }], self.admin.id)
        self.assertEqual(len(created), 2)
        self.assertTrue(all(item["initial_password"] for item in created))

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
