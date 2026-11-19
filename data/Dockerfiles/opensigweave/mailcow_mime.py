"""Conservative MIME rewriting for the Mailcow/Rspamd adapter."""
from __future__ import annotations

from dataclasses import dataclass
from email import policy
from email.generator import BytesGenerator
from email.parser import BytesParser
from io import BytesIO
import re

from core.signature_placement import (
    find_html_quote_boundary,
    find_plain_quote_boundary,
    insert_signature,
)

MAX_MESSAGE_BYTES = 20 * 1024 * 1024
MAX_MESSAGE_B64_CHARS = ((MAX_MESSAGE_BYTES + 2) // 3) * 4 + 16
_PROTECTED_TYPES = {
    "multipart/signed",
    "multipart/encrypted",
    "application/pkcs7-mime",
    "application/x-pkcs7-mime",
    "application/pgp-encrypted",
}
_SUPPORTED_CTE = {"7bit", "8bit", "quoted-printable", "base64"}


@dataclass(frozen=True)
class SelectorDirective:
    mode: str | None
    signature_name: str | None
    signature_code: str | None
    marker_present: bool
    signature_requested: bool


@dataclass(frozen=True)
class RewriteResult:
    message: bytes
    changed: bool
    reason: str
    signature_inserted: bool = False


def _contains_protected_part(part) -> bool:
    content_type = part.get_content_type().casefold()
    if part.get_content_maintype().casefold() == "message":
        return True
    if part.get_content_disposition() == "attachment" or part.get_filename():
        return False
    if content_type in _PROTECTED_TYPES:
        return True
    if part.is_multipart():
        return any(_contains_protected_part(child) for child in part.iter_parts())
    return False


def _editable_parts(part):
    if part.get_content_maintype().casefold() == "message":
        return
    if part.get_content_disposition() == "attachment" or part.get_filename():
        return
    if part.get_content_type().casefold() in _PROTECTED_TYPES:
        return
    yield part
    if part.is_multipart():
        for child in part.iter_parts():
            yield from _editable_parts(child)


def _trim_trailing_whitespace(content: str, *, html: bool) -> str:
    if not html:
        return content.rstrip().removesuffix("\u00a0").rstrip()
    previous = None
    while content != previous:
        previous = content
        content = content.rstrip()
        content = re.sub(r"<br\b[^>]*>\s*$", "", content, flags=re.I)
        content = re.sub(
            r"<(?:p|div)\b[^>]*>\s*(?:&nbsp;|\u00a0)?\s*</(?:p|div)>\s*$",
            "",
            content,
            flags=re.I,
        )
    return content


def _strip_device_signatures(content: str, *, html: bool) -> str:
    if html:
        return re.sub(
            r"<br\b[^>]*>\s*(?:Sent from my |Get Outlook for )[^<]*",
            "",
            content,
            flags=re.I,
        )
    return re.sub(
        r"\r?\n[ \t]*(?:Sent from my |Get Outlook for )[^\r\n<]*",
        "",
        content,
        flags=re.I,
    )


def _content_type_params(part) -> list[tuple[str, str]]:
    params = part.get_params(header="Content-Type", failobj=[]) or []
    preserved: list[tuple[str, str]] = []
    for key, value in params[1:]:
        if key.casefold() in {"charset", "boundary"}:
            continue
        if isinstance(value, str):
            preserved.append((key, value))
    return preserved


_VALID_MODES = {"AUTO", "PROFESSIONAL", "INTERNAL", "NONE"}
_MARKER_LITERAL = re.compile(
    r"<---\[([^\]\r\n]{1,255})\]--\[([^\]\r\n]{1,32})\]--->",
    re.I,
)
_MARKER_ESCAPED = re.compile(
    r"&lt;---\[([^\]\r\n]{1,255})\]--\[([^\]\r\n]{1,32})\]---&gt;",
    re.I,
)
_MARKER_PREFIX_LITERAL = re.compile(r"<---\[[^\r\n<]{0,512}", re.I)
_MARKER_PREFIX_ESCAPED = re.compile(r"&lt;---\[[^\r\n<]{0,512}", re.I)


def _newest_section(content: str, *, html: bool) -> str:
    boundary = find_html_quote_boundary(content) if html else find_plain_quote_boundary(content)
    return content if boundary is None else content[:boundary]


def extract_selector_directive(raw_message: bytes) -> SelectorDirective:
    parsed = BytesParser(policy=policy.default).parsebytes(raw_message)

    mode_values = [str(value).strip().upper() for value in parsed.get_all("X-OpenSigWeave-Mode", [])]
    mode = mode_values[0] if len(mode_values) == 1 and mode_values[0] in _VALID_MODES else None

    name_values = [str(value).strip() for value in parsed.get_all("X-OpenSigWeave-Signature", [])]
    code_values = [str(value).strip() for value in parsed.get_all("X-OpenSigWeave-Signature-Code", [])]
    header_signature_valid = len(name_values) == 1 and len(code_values) == 1
    header_name = name_values[0] if header_signature_valid else None
    header_code = code_values[0] if header_signature_valid else None

    marker_pairs: set[tuple[str, str]] = set()
    marker_present = False
    for part in _editable_parts(parsed):
        if part.is_multipart() or part.get_content_type().casefold() not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeError, TypeError, ValueError):
            continue
        if not isinstance(content, str):
            continue
        current = _newest_section(content, html=part.get_content_type().casefold() == "text/html")
        if _MARKER_PREFIX_LITERAL.search(current) or _MARKER_PREFIX_ESCAPED.search(current):
            marker_present = True
        for pattern in (_MARKER_LITERAL, _MARKER_ESCAPED):
            for match in pattern.finditer(current):
                marker_pairs.add((match.group(1).strip(), match.group(2).strip()))

    if mode is not None:
        return SelectorDirective(mode, None, None, marker_present, False)
    if header_signature_valid:
        return SelectorDirective(None, header_name, header_code, marker_present, True)
    if marker_present:
        if len(marker_pairs) == 1:
            name, code = next(iter(marker_pairs))
            return SelectorDirective(None, name, code, True, True)
        # Malformed/ambiguous marker becomes an explicit invalid selector.
        return SelectorDirective(None, "", "", True, True)
    return SelectorDirective(None, None, None, False, False)


def _remove_signature_markers(content: str, *, html: bool) -> tuple[str, bool]:
    boundary = find_html_quote_boundary(content) if html else find_plain_quote_boundary(content)
    before = content if boundary is None else content[:boundary]
    after = "" if boundary is None else content[boundary:]
    original = before

    for pattern in (_MARKER_LITERAL, _MARKER_ESCAPED):
        before = pattern.sub("", before)

    # Remove malformed marker prefixes only from the newest message section.
    before = _MARKER_PREFIX_LITERAL.sub("", before)
    before = _MARKER_PREFIX_ESCAPED.sub("", before)
    return before + after, before != original


def rewrite_message(
    raw_message: bytes,
    *,
    html_signature: str,
    plain_signature: str,
    inject_on_replies: bool,
    trim_whitespace: bool = True,
    strip_device_signatures: bool = False,
    cleanup_signature_markers: bool = False,
) -> RewriteResult:
    if len(raw_message) > MAX_MESSAGE_BYTES:
        raise ValueError("Message exceeds configured size limit")
    if not html_signature and not plain_signature and not cleanup_signature_markers:
        return RewriteResult(raw_message, False, "empty-signature")

    parsed = BytesParser(policy=policy.default).parsebytes(raw_message)
    if parsed.defects or _contains_protected_part(parsed):
        return RewriteResult(raw_message, False, "protected-or-malformed")

    reply_disabled = not inject_on_replies and (
        parsed.get("In-Reply-To") is not None or parsed.get("References") is not None
    )

    parts = list(_editable_parts(parsed))
    if any(part.defects for part in parts):
        return RewriteResult(raw_message, False, "part-defect")

    prepared = []
    marker_changed = False
    signature_inserted = False
    for part in parts:
        if part.is_multipart():
            continue
        content_type = part.get_content_type().casefold()
        if content_type not in {"text/plain", "text/html"}:
            continue
        transfer = str(part.get("Content-Transfer-Encoding", "7bit")).strip().casefold()
        if transfer not in _SUPPORTED_CTE:
            return RewriteResult(raw_message, False, "unsupported-transfer-encoding")
        try:
            content = part.get_content()
        except (LookupError, UnicodeError, TypeError, ValueError):
            return RewriteResult(raw_message, False, "undecodable-text-part")
        if not isinstance(content, str):
            return RewriteResult(raw_message, False, "non-text-content")

        is_html = content_type == "text/html"
        if cleanup_signature_markers:
            content, removed_marker = _remove_signature_markers(content, html=is_html)
            marker_changed = marker_changed or removed_marker

        signature = html_signature if is_html else plain_signature
        has_any_signature = bool(html_signature or plain_signature)
        if has_any_signature and not reply_disabled and not signature:
            return RewriteResult(raw_message, False, "missing-alternative-signature")

        if signature and not reply_disabled:
            if strip_device_signatures:
                content = _strip_device_signatures(content, html=is_html)
            if trim_whitespace:
                content = _trim_trailing_whitespace(content, html=is_html)
                signature = _trim_trailing_whitespace(signature, html=is_html).lstrip()
            updated, _quote_detected = insert_signature(
                content,
                signature,
                html=is_html,
                trim_whitespace=trim_whitespace,
            )
        else:
            updated = content
        charset = part.get_content_charset() or "utf-8"
        try:
            encoded = updated.encode(charset)
        except (LookupError, UnicodeError, TypeError):
            return RewriteResult(raw_message, False, "unsupported-charset")

        cte = transfer
        if cte == "7bit" and any(byte > 0x7F for byte in encoded):
            cte = "quoted-printable"

        if signature and not reply_disabled and updated != content:
            signature_inserted = True
        prepared.append(
            (part, updated, part.get_content_subtype(), charset, cte, _content_type_params(part))
        )

    if not prepared:
        return RewriteResult(raw_message, False, "no-editable-text-part")
    if reply_disabled and not marker_changed:
        return RewriteResult(raw_message, False, "reply-disabled")
    if not (signature_inserted or marker_changed):
        return RewriteResult(raw_message, False, "no-change")

    for part, updated, subtype, charset, cte, params in prepared:
        try:
            part.set_content(updated, subtype=subtype, charset=charset, cte=cte)
            for key, value in params:
                part.set_param(key, value, header="Content-Type")
        except (LookupError, UnicodeError, TypeError, ValueError):
            return RewriteResult(raw_message, False, "serialization-precondition-failed")

    output = BytesIO()
    BytesGenerator(output, policy=policy.SMTP).flatten(parsed)
    rewritten = output.getvalue()
    if not rewritten:
        return RewriteResult(raw_message, False, "serialization-failed")
    return RewriteResult(
        rewritten,
        True,
        "rewritten" if signature_inserted else "marker-cleanup",
        signature_inserted=signature_inserted,
    )
