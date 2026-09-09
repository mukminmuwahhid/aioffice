import unittest

from office import roles


class TestRoles(unittest.TestCase):
    def test_every_role_has_a_prompt_and_display_name(self):
        self.assertEqual(set(roles.ROLE_PROMPTS.keys()), set(roles.ROLE_DISPLAY_NAMES.keys()))
        self.assertEqual(set(roles.ROLE_IDS), set(roles.ROLE_PROMPTS.keys()))

    def test_ten_specialist_roles(self):
        self.assertEqual(len(roles.ROLE_IDS), 10)

    def test_chief_prompt_mentions_draft_only(self):
        self.assertIn("draft", roles.CHIEF_SYSTEM_PROMPT.lower())


if __name__ == "__main__":
    unittest.main()
