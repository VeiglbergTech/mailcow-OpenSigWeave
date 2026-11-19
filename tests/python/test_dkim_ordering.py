"""Guards for OpenSigWeave body mutation occurring before Mailcow DKIM signing."""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RSPAMD_LUA = (REPO_ROOT / "data" / "conf" / "rspamd" / "lua" / "rspamd.local.lua").read_text(encoding="utf-8")
DKIM_CONFIG = (REPO_ROOT / "data" / "conf" / "rspamd" / "local.d" / "dkim_signing.conf").read_text(encoding="utf-8")


class DkimOrderingTests(unittest.TestCase):
    def test_opensigweave_mail_mutation_is_a_prefilter(self):
        symbol = re.search(
            r"rspamd_config:register_symbol\(\{\s*"
            r"name\s*=\s*'MOO_FOOTER'.*?"
            r"type\s*=\s*'prefilter'",
            RSPAMD_LUA,
            flags=re.S,
        )
        self.assertIsNotNone(symbol, "MOO_FOOTER must remain a Rspamd prefilter")

    def test_mailcow_dkim_signing_covers_mutated_mime_headers(self):
        self.assertIn('symbol = "DKIM_SIGNED";', DKIM_CONFIG)
        match = re.search(r'sign_headers\s*=\s*"([^"]+)"', DKIM_CONFIG)
        self.assertIsNotNone(match)
        headers = {item.strip().casefold() for item in match.group(1).split(":")}
        self.assertIn("content-type", headers)
        self.assertIn("content-transfer-encoding", headers)
        self.assertIn("mime-version", headers)

    def test_opensigweave_rewrite_happens_inside_prefilter_before_return(self):
        block_start = RSPAMD_LUA.index("name = 'MOO_FOOTER'")
        block_end = RSPAMD_LUA.index("priority = 1", block_start)
        block = RSPAMD_LUA[block_start:block_end]
        self.assertIn("/internal/process-message", block)
        self.assertIn("task:set_message(rewrite_body)", block)


if __name__ == "__main__":
    unittest.main()
