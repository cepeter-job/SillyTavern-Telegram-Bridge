"""Shared on/off normalization must preserve both public preference contracts."""

import unittest

from bridge.grounded_user_settings import normalize_grounded_user
from bridge.humanizer_settings import normalize_humanizer


class BooleanSettingsTests(unittest.TestCase):
    def test_both_preferences_normalize_the_same_aliases(self):
        for value, expected in (
            ("on", "on"),
            (" OFF ", "off"),
            (" TRUE ", "on"),
            ("yes", "on"),
            ("Enabled", "on"),
            ("1", "on"),
            ("false", "off"),
            ("no", "off"),
            ("disabled", "off"),
            ("0", "off"),
        ):
            for normalize in (normalize_grounded_user, normalize_humanizer):
                with self.subTest(value=value, preference=normalize.__name__):
                    self.assertEqual(normalize(value), expected)

    def test_both_preferences_reject_unknown_and_empty_values(self):
        for value in (None, "", " ", "sometimes", "onwards", "yes please", "1.5"):
            for normalize in (normalize_grounded_user, normalize_humanizer):
                with self.subTest(value=value, preference=normalize.__name__):
                    with self.assertRaisesRegex(ValueError, "use on or off"):
                        normalize(value)
