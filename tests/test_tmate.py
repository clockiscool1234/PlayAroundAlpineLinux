from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bot.tmate import TmateSettings, build_command, build_config, ssh_fingerprint

ED25519_PUBLIC_KEY = (
    "ssh-ed25519 "
    "AAAAC3NzaC1lZDI1NTE5AAAAINWjhYCyE9QIU5qdsk6cv9jXcR9HPNJls3UZojBiVtQ2 "
    "test"
)
ED25519_FINGERPRINT = "SHA256:Yd3C2zj09laqfVyxCqyzO8wjv5d+RpfhfwgaYqWXcxs"


class TmateFingerprintTests(unittest.TestCase):
    def test_openssh_sha256_fingerprint(self) -> None:
        self.assertEqual(ssh_fingerprint(ED25519_PUBLIC_KEY), ED25519_FINGERPRINT)

    def test_rejects_malformed_public_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "not an SSH public key"):
            ssh_fingerprint("not-a-key")


class TmateSettingsTests(unittest.TestCase):
    def test_disabled_without_host(self) -> None:
        settings = TmateSettings(ed25519_fingerprint=ED25519_FINGERPRINT)

        self.assertFalse(settings.configured)

    def test_reads_fingerprint_from_keys_directory(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        key = Path(directory.name) / "ssh_host_ed25519_key.pub"
        key.write_text(ED25519_PUBLIC_KEY + "\n", encoding="utf-8")

        settings = TmateSettings(
            server_host="tmate", server_port=2222, keys_path=Path(directory.name)
        )

        self.assertTrue(settings.configured)
        self.assertEqual(settings.fingerprints(), ("", ED25519_FINGERPRINT))

    def test_rejects_whitespace_and_bad_ports(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "between 1 and 65535"):
            TmateSettings(server_port=0)
        with self.assertRaisesRegex(RuntimeError, "may contain only"):
            TmateSettings(server_host="bad host", ed25519_fingerprint="x")

    def test_config_enables_fingerprint_check(self) -> None:
        settings = TmateSettings(
            server_host="tmate",
            server_port=2222,
            rsa_fingerprint="SHA256:" + "a" * 43,
            ed25519_fingerprint=ED25519_FINGERPRINT,
        )

        config = build_config(settings)

        self.assertIn("tmate-server-host tmate", config)
        self.assertIn("tmate-server-port 2222", config)
        self.assertIn(f"tmate-server-ed25519-fingerprint {ED25519_FINGERPRINT}", config)

    def test_start_command_writes_config_and_waits(self) -> None:
        settings = TmateSettings(
            server_host="tmate",
            server_port=2222,
            ed25519_fingerprint=ED25519_FINGERPRINT,
        )

        command = build_command(settings)

        self.assertEqual(command[:2], ["/bin/sh", "-c"])
        self.assertIn("tmate -f /tmp/alpine-tmate.conf", command[2])
        self.assertIn("wait tmate-ready", command[2])
        self.assertIn("#{tmate_ssh}", command[2])

    def test_stop_does_not_need_server_settings(self) -> None:
        command = build_command(TmateSettings(), stop=True)

        self.assertIn("kill-server", command[2])
        self.assertIn("tmate session stopped", command[2])


if __name__ == "__main__":
    unittest.main()
