"""Regression checks for the Mailcow OpenSigWeave PHP gateway allowlist."""
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATEWAY = REPO_ROOT / "data" / "web" / "inc" / "ajax" / "opensigweave.php"


class MailcowOpenSigWeaveGatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = GATEWAY.read_text(encoding="utf-8")

    def test_gateway_requires_mailcow_admin_session_and_csrf(self):
        self.assertIn("protect_route(['admin']);", self.source)
        self.assertIn("HTTP_X_CSRF_TOKEN", self.source)
        self.assertIn("hash_equals($expected, $csrf)", self.source)

    def test_gateway_uses_dedicated_admin_key(self):
        self.assertIn("OPENSIGWEAVE_ADMIN_KEY", self.source)
        self.assertIn("'X-Admin-Key: ' . $adminKey", self.source)
        self.assertNotIn("OPENSIGWEAVE_ENGINE_KEY", self.source)

    def test_gateway_allowlist_covers_every_management_resource(self):
        self.assertRegex(self.source, r"in_array\(\$path, \['/admin/groups', '/admin/rules'\], true\)")
        self.assertRegex(
            self.source,
            r"\^/admin/\(groups\|rules\)/\[a-f0-9\]\{32\}\$",
        )
        self.assertRegex(
            self.source,
            r"\^/admin/signatures/\[a-f0-9\]\{32\}\$",
        )
        self.assertRegex(self.source, r"\^/admin/domains/\[0-9\]\+\$")

    def test_gateway_does_not_accept_arbitrary_engine_paths(self):
        self.assertIn("if (!$allowed)", self.source)
        self.assertIn("Unsupported administration operation", self.source)


if __name__ == "__main__":
    unittest.main()
