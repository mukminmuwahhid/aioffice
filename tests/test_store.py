import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class StoreTestCase(unittest.TestCase):
    """Each test gets a throwaway .office/ so it never touches real settings."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self._tmp.name)

        from office import store
        self.store = store
        self._patches = [
            patch.object(store, "STORE_DIR", tmp_path),
            patch.object(store, "SETTINGS_FILE", tmp_path / "settings.json"),
            patch.object(store, "ROLES_FILE", tmp_path / "roles.json"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()


class TestSettings(StoreTestCase):
    def test_defaults_when_nothing_saved(self):
        self.assertEqual(self.store.load_settings(), self.store.DEFAULT_SETTINGS)

    def test_save_merges_and_persists(self):
        self.store.save_settings({"max_retries": 5})
        settings = self.store.load_settings()
        self.assertEqual(settings["max_retries"], 5)
        # Untouched keys keep their defaults.
        self.assertEqual(settings["max_parallel"], self.store.DEFAULT_SETTINGS["max_parallel"])

    def test_unknown_keys_are_ignored(self):
        self.store.save_settings({"not_a_setting": "x", "max_parallel": 3})
        settings = self.store.load_settings()
        self.assertNotIn("not_a_setting", settings)
        self.assertEqual(settings["max_parallel"], 3)

    def test_reset_restores_defaults(self):
        self.store.save_settings({"max_retries": 5})
        self.store.reset_settings()
        self.assertEqual(self.store.load_settings(), self.store.DEFAULT_SETTINGS)

    def test_corrupt_file_falls_back_to_defaults(self):
        self.store.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.store.SETTINGS_FILE.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.store.load_settings(), self.store.DEFAULT_SETTINGS)

    def test_validation_rejects_bad_ranges_and_types(self):
        with self.assertRaises(self.store.ValidationError) as caught:
            self.store.validate_settings(
                {"max_retries": -1, "mock_mode": "yes"}, ["claude-test"]
            )
        self.assertIn("max_retries", caught.exception.errors)
        self.assertIn("mock_mode", caught.exception.errors)

    def test_validation_rejects_unknown_model(self):
        with self.assertRaises(self.store.ValidationError):
            self.store.validate_settings(
                {"default_model": "made-up-model"}, ["claude-test"]
            )


class TestRoleOverrides(StoreTestCase):
    def test_override_round_trip(self):
        self.store.save_role_override("qa_engineer", {"name": "Test Lead", "enabled": False})
        saved = self.store.load_role_overrides()["qa_engineer"]
        self.assertEqual(saved["name"], "Test Lead")
        self.assertFalse(saved["enabled"])

    def test_override_merges_rather_than_replaces(self):
        self.store.save_role_override("qa_engineer", {"name": "Test Lead"})
        self.store.save_role_override("qa_engineer", {"model": "claude-haiku-4-5"})
        saved = self.store.load_role_overrides()["qa_engineer"]
        self.assertEqual(saved["name"], "Test Lead")
        self.assertEqual(saved["model"], "claude-haiku-4-5")

    def test_unknown_fields_are_ignored(self):
        self.store.save_role_override("qa_engineer", {"sudo": True, "name": "Test Lead"})
        self.assertNotIn("sudo", self.store.load_role_overrides()["qa_engineer"])

    def test_reset_removes_only_that_role(self):
        self.store.save_role_override("qa_engineer", {"name": "Test Lead"})
        self.store.save_role_override("backend_developer", {"name": "API Dev"})
        self.store.reset_role_override("qa_engineer")
        overrides = self.store.load_role_overrides()
        self.assertNotIn("qa_engineer", overrides)
        self.assertIn("backend_developer", overrides)

    def test_validation_rejects_bad_colour_and_empty_prompt(self):
        with self.assertRaises(self.store.ValidationError) as caught:
            self.store.validate_role_override(
                {"accent": "green", "prompt": ""}, ["claude-test"]
            )
        self.assertIn("accent", caught.exception.errors)
        self.assertIn("prompt", caught.exception.errors)


class TestRolesWithOverrides(StoreTestCase):
    def setUp(self):
        super().setUp()
        from office import roles
        self.roles = roles

    def test_meta_defaults_to_stock_prompt(self):
        meta = self.roles.role_meta("qa_engineer")
        self.assertEqual(meta["prompt"], self.roles.ROLE_PROMPTS["qa_engineer"])
        self.assertFalse(meta["customised"])

    def test_override_applies_to_accessors(self):
        self.store.save_role_override("qa_engineer", {"name": "Test Lead", "prompt": "Custom brief."})
        self.assertEqual(self.roles.display_name("qa_engineer"), "Test Lead")
        self.assertEqual(self.roles.role_prompt("qa_engineer"), "Custom brief.")
        self.assertTrue(self.roles.role_meta("qa_engineer")["customised"])

    def test_blank_value_falls_back_to_default(self):
        self.store.save_role_override("qa_engineer", {"name": ""})
        self.assertEqual(
            self.roles.display_name("qa_engineer"),
            self.roles.ROLE_DISPLAY_NAMES["qa_engineer"],
        )

    def test_disabled_role_drops_out_of_enabled_ids(self):
        self.store.save_role_override("qa_engineer", {"enabled": False})
        enabled = self.roles.enabled_role_ids()
        self.assertNotIn("qa_engineer", enabled)
        self.assertEqual(len(enabled), len(self.roles.ROLE_IDS) - 1)


if __name__ == "__main__":
    unittest.main()
