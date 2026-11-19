from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bot.config import UserSettingsDefaults


class UserSettingsDefaultsTests(unittest.TestCase):
    def write(self, text: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "defaults.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_loads_terminal_defaults(self) -> None:
        path = self.write(
            """
[terminal]
columns = 100
rows = 17
idle_minutes = 0
rich_embed = false
refresh_seconds = 9
"""
        )

        defaults = UserSettingsDefaults.from_toml(path)

        self.assertEqual(defaults.columns, 100)
        self.assertEqual(defaults.rows, 17)
        self.assertEqual(defaults.idle_minutes, 0)
        self.assertFalse(defaults.rich_embed)
        self.assertEqual(defaults.refresh_seconds, 9)

    def test_rejects_out_of_range_values(self) -> None:
        path = self.write(
            """
[terminal]
columns = 121
"""
        )

        with self.assertRaisesRegex(RuntimeError, "between 20 and 120"):
            UserSettingsDefaults.from_toml(path)

    def test_rejects_unknown_values(self) -> None:
        path = self.write(
            """
[terminal]
columns = 80
unknown = true
"""
        )

        with self.assertRaisesRegex(RuntimeError, "Unknown terminal default"):
            UserSettingsDefaults.from_toml(path)


if __name__ == "__main__":
    unittest.main()
