from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.config import Config, UserSettingsDefaults


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


class ConfigNetworkTests(unittest.TestCase):
    def load(self, **environment: str) -> Config:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        defaults = root / "defaults.toml"
        defaults.write_text("[terminal]\n", encoding="utf-8")

        values = {
            "DISCORD_TOKEN": "token",
            "USER_DEFAULTS_PATH": str(defaults),
            "DATABASE_PATH": str(root / "bot.db"),
        }
        values.update(environment)
        with mock.patch.dict(os.environ, values, clear=True):
            return Config.from_env()

    def test_network_is_enabled_by_default(self) -> None:
        config = self.load()

        self.assertTrue(config.container_network_enabled)
        self.assertEqual(config.container_network, "alpine-filtered")
        self.assertEqual(
            config.container_http_proxy,
            "http://172.30.0.2:3128",
        )

    def test_network_can_be_disabled(self) -> None:
        config = self.load(
            CONTAINER_NETWORK_ENABLED="false",
            CONTAINER_NETWORK="a-custom-network",
            CONTAINER_HTTP_PROXY="http://proxy:3128",
        )

        self.assertFalse(config.container_network_enabled)
        self.assertEqual(config.container_network, "none")
        self.assertEqual(config.container_http_proxy, "")

    def test_invalid_network_toggle_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be true or false"):
            self.load(CONTAINER_NETWORK_ENABLED="sometimes")

    def test_lxcfs_path_is_optional(self) -> None:
        self.assertEqual(self.load().container_lxcfs_path, "")
        self.assertEqual(
            self.load(CONTAINER_LXCFS_PATH="/var/lib/lxcfs").container_lxcfs_path,
            "/var/lib/lxcfs",
        )

    def test_tmate_settings_are_loaded(self) -> None:
        fingerprint = "SHA256:" + "a" * 43
        config = self.load(
            TMATE_SERVER_HOST="relay.example",
            TMATE_SERVER_PORT="2222",
            TMATE_KEYS_PATH="/tmp/tmate-keys",
            TMATE_SERVER_ED25519_FINGERPRINT=fingerprint,
        )

        self.assertEqual(config.tmate.server_host, "relay.example")
        self.assertEqual(config.tmate.server_port, 2222)
        self.assertEqual(str(config.tmate.keys_path), "/tmp/tmate-keys")
        self.assertEqual(config.tmate.ed25519_fingerprint, fingerprint)
        self.assertTrue(config.tmate.configured)


if __name__ == "__main__":
    unittest.main()
