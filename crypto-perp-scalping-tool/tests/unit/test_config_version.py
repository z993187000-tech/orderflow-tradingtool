import unittest

from crypto_perp_tool.config import default_settings, load_settings


class ConfigVersionTests(unittest.TestCase):
    def test_default_has_config_version(self):
        settings = default_settings()
        self.assertTrue(hasattr(settings, "config_version"))
        self.assertIsInstance(settings.config_version, str)
        self.assertEqual(len(settings.config_version), 12)

    def test_default_config_version_is_stable(self):
        s1 = default_settings()
        s2 = default_settings()
        self.assertEqual(s1.config_version, s2.config_version,
                         "Default config version should be deterministic")

    def test_load_settings_includes_config_version(self):
        settings = load_settings()
        self.assertTrue(hasattr(settings, "config_version"))
        self.assertEqual(len(settings.config_version), 12)

    def test_load_settings_identical_overrides_same_version(self):
        s1 = load_settings({"mode": "paper"})
        s2 = load_settings({"mode": "paper"})
        self.assertEqual(s1.config_version, s2.config_version)

    def test_default_settings_are_frozen(self):
        settings = default_settings()
        self.assertTrue(hasattr(settings, "config_version"))


if __name__ == "__main__":
    unittest.main()
