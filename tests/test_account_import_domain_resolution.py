import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from web_app.backend import account_api
from web_app.backend.account_api import ImportDomainResolutionRequest
from web_app.backend.identity_store import IdentityStore, engineer_preferences, sessions


class AccountImportDomainResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        database = Path(self.tempdir.name) / "identity.db"
        self.store = IdentityStore(f"sqlite:///{database.as_posix()}")
        self.admin = self.store.authenticate(
            "admin", "ForceconAdmin!2026", "forcecon.com.tw"
        )
        self.assertIsNotNone(self.admin)

    def tearDown(self):
        self.store.close()
        self.tempdir.cleanup()

    def test_valid_rows_are_created_and_unknown_domains_wait_for_review(self):
        rows = [
            {"email": "valid@forcecon.com.tw", "display_name": "Valid"},
            {"email": "typo@forcecon.con.tw", "display_name": "Typo"},
            {"email": "partner@partner-forcecon.com", "display_name": "Partner"},
            {"email": "missing@forcecon.com.tw", "display_name": ""},
        ]
        with patch.object(account_api, "store", self.store):
            created, unresolved, rejected = account_api._import_rows_with_domain_review(
                rows, self.admin.id
            )
            self.assertEqual([item["email"] for item in created], ["valid@forcecon.com.tw"])
            self.assertEqual(len(unresolved), 2)
            self.assertEqual(len(rejected), 1)

            result = account_api.admin_resolve_import_domains(
                ImportDomainResolutionRequest(
                    rows=unresolved,
                    resolutions={
                        "forcecon.con.tw": "forcecon.com.tw",
                        "partner-forcecon.com": "__add__",
                    },
                ),
                admin=self.admin,
            )

        self.assertEqual(result["created_count"], 2)
        self.assertEqual(result["domains_added"], ["partner-forcecon.com"])
        self.assertEqual({item["email"] for item in result["users"]}, {
            "typo@forcecon.com.tw",
            "partner@partner-forcecon.com",
        })
        self.assertIn("partner-forcecon.com", self.store.list_company_email_domains())

    def test_account_delete_cleans_related_records_and_protects_current_admin(self):
        target = self.store.create_user({
            "email": "delete.me@forcecon.com.tw",
            "display_name": "Delete Me",
            "role": "ENGINEER",
        }, self.admin.id)
        self.store.create_session(target["id"])

        deleted = self.store.delete_users([target["id"]], self.admin.id)

        self.assertEqual([item["id"] for item in deleted], [target["id"]])
        self.assertNotIn(target["id"], {item["id"] for item in self.store.list_users()})
        with self.store.engine.connect() as conn:
            self.assertIsNone(conn.execute(select(sessions).where(
                sessions.c.user_id == target["id"]
            )).first())
            self.assertIsNone(conn.execute(select(engineer_preferences).where(
                engineer_preferences.c.user_id == target["id"]
            )).first())
        with self.assertRaisesRegex(ValueError, "active account"):
            self.store.delete_users([self.admin.id], self.admin.id)


if __name__ == "__main__":
    unittest.main()
