from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "network-filter" / "generate_blocklists.py"
SPEC = importlib.util.spec_from_file_location("generate_blocklists", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
generate_blocklists = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generate_blocklists)


class BlocklistGeneratorTests(unittest.TestCase):
    def test_normalizes_domains_urls_hosts_and_adblock_rules(self) -> None:
        normalize = generate_blocklists.normalize_domain
        self.assertEqual(normalize("https://www.Example.COM/path"), "example.com")
        self.assertEqual(normalize("||bad.example^"), "bad.example")
        self.assertEqual(normalize("0.0.0.0 blocked.example"), "blocked.example")
        self.assertIsNone(normalize("192.0.2.10"))
        self.assertIsNone(normalize("# comment"))

    def test_parent_domain_removes_redundant_subdomains(self) -> None:
        collapsed = generate_blocklists.collapse_subdomains(
            {"example.com", "a.example.com", "other.example"}
        )
        self.assertEqual(collapsed, ["example.com", "other.example"])


if __name__ == "__main__":
    unittest.main()
