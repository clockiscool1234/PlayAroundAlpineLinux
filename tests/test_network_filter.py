from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "network-filter" / "configure-upstream.sh"


class UpstreamConfigurationTests(unittest.TestCase):
    def configure(self, **variables: str) -> tuple[subprocess.CompletedProcess[str], str]:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        output = Path(directory.name) / "upstream.conf"
        environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        environment.update(variables)
        result = subprocess.run(
            ["/bin/sh", str(SCRIPT), str(output)],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        return result, output.read_text() if output.exists() else ""

    def test_direct_mode_writes_empty_file(self) -> None:
        result, content = self.configure()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(content, "")
        self.assertIn("direct", result.stdout)

    def test_vpn_upstream_fails_closed_with_authentication(self) -> None:
        result, content = self.configure(
            VPN_UPSTREAM_PROXY="vpn:8888",
            HTTPPROXY_USER="alpinevpn",
            HTTPPROXY_PASSWORD="secret-value",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "cache_peer vpn parent 8888 0 no-query default "
            "name=vpn_upstream login=alpinevpn:secret-value",
            content,
        )
        self.assertIn("never_direct allow all", content)

    def test_rejects_invalid_proxy_address(self) -> None:
        result, _ = self.configure(VPN_UPSTREAM_PROXY="vpn")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected host:port", result.stderr)

    def test_rejects_incomplete_proxy_credentials(self) -> None:
        result, _ = self.configure(
            VPN_UPSTREAM_PROXY="vpn:8888",
            HTTPPROXY_USER="alpinevpn",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must both be set", result.stderr)

    def test_rejects_unsafe_proxy_credentials(self) -> None:
        result, _ = self.configure(
            VPN_UPSTREAM_PROXY="vpn:8888",
            HTTPPROXY_USER="alpinevpn",
            HTTPPROXY_PASSWORD="bad value",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("may contain only", result.stderr)


if __name__ == "__main__":
    unittest.main()