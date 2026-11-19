"""Regression tests for Mailcow-safe MIME signature rewriting."""
import unittest
from email import policy
from email.parser import BytesParser

from mailcow_mime import extract_selector_directive, rewrite_message


class MailcowMimeRewriteTests(unittest.TestCase):
    def test_valid_mode_header_precedes_body_marker(self):
        raw = (
            b"X-OpenSigWeave-Mode: INTERNAL\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Hello\r\n<---[Holiday]--[abcd]--->\r\n"
        )
        directive = extract_selector_directive(raw)
        self.assertEqual(directive.mode, "INTERNAL")
        self.assertIsNone(directive.signature_name)
        self.assertTrue(directive.marker_present)

    def test_signature_header_precedes_body_marker_when_mode_is_absent(self):
        raw = (
            b"X-OpenSigWeave-Signature: Header Signature\r\n"
            b"X-OpenSigWeave-Signature-Code: 1234\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"<---[Body Signature]--[abcd]--->\r\n"
        )
        directive = extract_selector_directive(raw)
        self.assertEqual(directive.signature_name, "Header Signature")
        self.assertEqual(directive.signature_code, "1234")
        self.assertTrue(directive.marker_present)

    def test_marker_in_quoted_history_is_not_selected_or_removed(self):
        raw = (
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"New answer\r\n\r\n> <---[Old Signature]--[abcd]--->\r\n"
        )
        directive = extract_selector_directive(raw)
        self.assertFalse(directive.marker_present)
        result = rewrite_message(
            raw,
            html_signature="",
            plain_signature="",
            inject_on_replies=True,
            cleanup_signature_markers=directive.marker_present,
        )
        self.assertFalse(result.changed)
        self.assertEqual(result.message, raw)

    def test_body_marker_is_cleaned_from_newest_section(self):
        raw = (
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Hello\r\n<---[Holiday]--[abcd]--->\r\n"
        )
        directive = extract_selector_directive(raw)
        self.assertTrue(directive.marker_present)
        result = rewrite_message(
            raw,
            html_signature="",
            plain_signature="",
            inject_on_replies=True,
            cleanup_signature_markers=True,
        )
        self.assertTrue(result.changed)
        body = BytesParser(policy=policy.default).parsebytes(result.message).get_content()
        self.assertNotIn("<---[", body)

    def test_plain_reply_signature_is_inserted_before_quote(self):
        raw = (
            b"From: alice@example.org\r\n"
            b"To: bob@example.org\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"New answer\r\n\r\n> old answer\r\n"
        )
        result = rewrite_message(
            raw,
            html_signature="<p>Signature</p>",
            plain_signature="Signature",
            inject_on_replies=True,
        )
        self.assertTrue(result.changed)
        body = BytesParser(policy=policy.default).parsebytes(result.message).get_content()
        self.assertLess(body.index("Signature"), body.index("> old answer"))

    def test_outlook_html_reply_container_is_preserved_after_signature(self):
        raw = (
            b"From: alice@example.org\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n\r\n"
            b"<html><body><div>New answer</div>"
            b"<div id=\"divRplyFwdMsg\"><b>From:</b> Bob</div></body></html>"
        )
        result = rewrite_message(
            raw,
            html_signature="<p>Signature</p>",
            plain_signature="Signature",
            inject_on_replies=True,
        )
        self.assertTrue(result.changed)
        body = BytesParser(policy=policy.default).parsebytes(result.message).get_content()
        self.assertLess(body.index("Signature"), body.index("divRplyFwdMsg"))

    def test_multipart_alternative_updates_plain_and_html_together(self):
        raw = (
            b"From: alice@example.org\r\nMIME-Version: 1.0\r\n"
            b"Content-Type: multipart/alternative; boundary=alt\r\n\r\n"
            b"--alt\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nHello\r\n"
            b"--alt\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
            b"<html><body>Hello</body></html>\r\n--alt--\r\n"
        )
        result = rewrite_message(
            raw,
            html_signature="<p>Signature</p>",
            plain_signature="Signature",
            inject_on_replies=True,
        )
        self.assertTrue(result.changed)
        parsed = BytesParser(policy=policy.default).parsebytes(result.message)
        contents = [part.get_content() for part in parsed.iter_parts()]
        self.assertIn("Signature", contents[0])
        self.assertIn("Signature", contents[1])

    def test_binary_attachment_payload_survives_rewrite(self):
        raw = (
            b"From: alice@example.org\r\nMIME-Version: 1.0\r\n"
            b"Content-Type: multipart/mixed; boundary=mix\r\n\r\n"
            b"--mix\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nHello\r\n"
            b"--mix\r\nContent-Type: application/octet-stream\r\n"
            b"Content-Disposition: attachment; filename=test.bin\r\n"
            b"Content-Transfer-Encoding: base64\r\n\r\nAAECAwQF\r\n"
            b"--mix--\r\n"
        )
        original = BytesParser(policy=policy.default).parsebytes(raw)
        original_payload = list(original.iter_attachments())[0].get_payload(decode=True)
        result = rewrite_message(
            raw,
            html_signature="<p>Signature</p>",
            plain_signature="Signature",
            inject_on_replies=True,
        )
        self.assertTrue(result.changed)
        rewritten = BytesParser(policy=policy.default).parsebytes(result.message)
        rewritten_payload = list(rewritten.iter_attachments())[0].get_payload(decode=True)
        self.assertEqual(rewritten_payload, original_payload)

    def test_signed_message_is_left_byte_identical(self):
        raw = (
            b"From: alice@example.org\r\nMIME-Version: 1.0\r\n"
            b"Content-Type: multipart/signed; boundary=sig; protocol=application/pgp-signature\r\n\r\n"
            b"--sig\r\nContent-Type: text/plain\r\n\r\nSigned body\r\n"
            b"--sig\r\nContent-Type: application/pgp-signature\r\n\r\nsignature\r\n"
            b"--sig--\r\n"
        )
        result = rewrite_message(
            raw,
            html_signature="<p>Signature</p>",
            plain_signature="Signature",
            inject_on_replies=True,
        )
        self.assertFalse(result.changed)
        self.assertEqual(result.message, raw)

    def test_attached_message_rfc822_fails_closed(self):
        raw = (
            b"From: alice@example.org\r\nMIME-Version: 1.0\r\n"
            b"Content-Type: multipart/mixed; boundary=mix\r\n\r\n"
            b"--mix\r\nContent-Type: text/plain\r\n\r\nOuter body\r\n"
            b"--mix\r\nContent-Type: message/rfc822\r\n\r\n"
            b"From: forwarded@example.org\r\nContent-Type: text/plain\r\n\r\nForwarded body\r\n"
            b"--mix--\r\n"
        )
        result = rewrite_message(
            raw,
            html_signature="<p>Signature</p>",
            plain_signature="Signature",
            inject_on_replies=True,
        )
        self.assertFalse(result.changed)
        self.assertEqual(result.reason, "protected-or-malformed")

    def test_reply_headers_disable_rewrite_when_policy_is_off(self):
        raw = (
            b"From: alice@example.org\r\nIn-Reply-To: <old@example.org>\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\nAnswer\r\n"
        )
        result = rewrite_message(
            raw,
            html_signature="<p>Signature</p>",
            plain_signature="Signature",
            inject_on_replies=False,
        )
        self.assertFalse(result.changed)
        self.assertEqual(result.reason, "reply-disabled")

    def test_strip_device_signature_policy_is_applied(self):
        raw = (
            b"From: alice@example.org\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Hello\r\nSent from my iPhone\r\n"
        )
        result = rewrite_message(
            raw,
            html_signature="<p>Signature</p>",
            plain_signature="Signature",
            inject_on_replies=True,
            strip_device_signatures=True,
        )
        body = BytesParser(policy=policy.default).parsebytes(result.message).get_content()
        self.assertNotIn("Sent from my iPhone", body)
        self.assertIn("Signature", body)


if __name__ == "__main__":
    unittest.main()
