"""Static integration checks for the Mailcow Rspamd OpenSigWeave bridge."""
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LUA = (REPO_ROOT / "data" / "conf" / "rspamd" / "lua" / "rspamd.local.lua").read_text(encoding="utf-8")
FOOTER = (REPO_ROOT / "data" / "conf" / "rspamd" / "dynmaps" / "footer.php").read_text(encoding="utf-8")
COMPOSE = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")


class RspamdOpenSigWeaveAdapterTests(unittest.TestCase):
    def test_engine_render_result_is_marked_for_parser_based_rewrite(self):
        self.assertIn("'opensigweave' => true", FOOTER)
        self.assertIn("'commit_selection' => false", FOOTER)
        self.assertIn("'context' => [", FOOTER)

    def test_rspamd_uses_full_message_rewrite_only_for_opensigweave(self):
        marker = "if footer.opensigweave == true then"
        self.assertIn(marker, LUA)
        self.assertIn("task:get_message()", LUA)
        self.assertIn("rspamd_util.encode_base64", LUA)
        self.assertIn('local rspamd_util = require "rspamd_util"', LUA)
        self.assertIn("/internal/process-message", LUA)
        self.assertIn("task:set_message(rewrite_body)", LUA)
        self.assertLess(LUA.index(marker), LUA.index("lua_mime.add_text_footer", LUA.index(marker)))

    def test_selector_headers_are_consumed_and_preflighted_without_commit(self):
        self.assertIn("X-OpenSigWeave-Mode", LUA)
        self.assertIn("X-OpenSigWeave-Signature", LUA)
        self.assertIn("X-OpenSigWeave-Signature-Code", LUA)
        self.assertIn("task:modify_header(control_name", LUA)
        self.assertIn("Osw-Selector-Hint", LUA)
        self.assertIn("'commit_selection' => false", FOOTER)

    def test_native_mailcow_footer_path_is_retained(self):
        self.assertIn("Native Mailcow footer fallback keeps the upstream Rspamd path.", LUA)
        self.assertIn("lua_mime.add_text_footer(task, footer.html, footer.plain)", LUA)

    def test_rspamd_receives_private_engine_key(self):
        self.assertIn("OPENSIGWEAVE_ENGINE_KEY=" + "$" + "{OPENSIGWEAVE_ENGINE_KEY}", COMPOSE)
        self.assertIn("['X-Engine-Key'] = engine_key", LUA)


if __name__ == "__main__":
    unittest.main()
