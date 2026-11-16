"""Private, headless OpenSigWeave signature engine for Mailcow."""
import hashlib
import hmac
import logging
import os
import re
import uuid
from contextlib import contextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from jinja2 import TemplateError
from markupsafe import Markup
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import create_database_engine, create_session_factory
from models import (
    DomainDB, GroupSelectionStateDB, SignatureDB, SignatureDomainDB,
    SignatureGroupDB, SignatureGroupMemberDB, SignatureRuleDB,
    SignatureVersionDB, UserOverrideDB,
)
from core.group_selection_repository import select_and_record_group_signature
from core.modes import SignatureMode, resolve_mode
from core.rule_repository import load_signature_rules
from core.rules import RuleContext, evaluate_rules
from core.selection_repository import select_and_record_variant
from signature_renderer import (
    prepare_group_placeholders, render_signature_template, strip_group_placeholders,
)
from signature_sanitizer import sanitize_signature_html
from signature_storage import SignatureFileStore, SignatureStorageError

logger = logging.getLogger("opensigweave")
ENGINE_API_KEY = os.environ.get("ENGINE_API_KEY", "")
if len(ENGINE_API_KEY) < 32:
    raise RuntimeError("ENGINE_API_KEY must contain at least 32 characters")

DATABASE_ENGINE = create_database_engine()
SessionLocal = create_session_factory(DATABASE_ENGINE)
SIGNATURE_STORAGE_PATH = os.environ.get("SIGNATURE_STORAGE_PATH", "/var/lib/opensigweave")
signature_file_store = SignatureFileStore(SIGNATURE_STORAGE_PATH)

EMAIL_PATTERN = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")


class MailcowProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    principal_id: str | None = Field(default=None, max_length=320)
    first_name: str = Field(default="", max_length=256)
    last_name: str = Field(default="", max_length=256)
    title: str = Field(default="", max_length=256)
    phone: str = Field(default="", max_length=256)
    groups: list[str] = Field(default_factory=list, max_length=256)
    claims: dict[str, Any] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)


class SignatureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authenticated_user: str = Field(min_length=3, max_length=254)
    envelope_sender: str = Field(min_length=3, max_length=254)
    target_domain: str = Field(min_length=3, max_length=253)
    profile: MailcowProfile = Field(default_factory=MailcowProfile)
    mode: str | None = Field(default=None, max_length=32)
    signature_name: str | None = Field(default=None, max_length=255)
    signature_code: str | None = Field(default=None, max_length=4)


def get_db():
    with SessionLocal() as session:
        yield session


def require_engine_key(x_engine_key: str | None = Header(default=None, alias="X-Engine-Key")):
    if not hmac.compare_digest(x_engine_key or "", ENGINE_API_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized engine request")


app = FastAPI(
    title="OpenSigWeave Mailcow Engine",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/internal/signature", dependencies=[Depends(require_engine_key)])
def render_mailcow_signature(
    request: SignatureRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    authenticated_user = request.authenticated_user.strip().casefold()
    envelope_sender = request.envelope_sender.strip().casefold()
    target_domain = request.target_domain.strip().rstrip(".").casefold()
    if not EMAIL_PATTERN.fullmatch(authenticated_user):
        raise HTTPException(status_code=400, detail="Invalid authenticated mailbox")
    if not EMAIL_PATTERN.fullmatch(envelope_sender):
        raise HTTPException(status_code=400, detail="Invalid envelope sender")
    if len(target_domain) > 253 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", target_domain):
        raise HTTPException(status_code=400, detail="Invalid target domain")

    domain_part = target_domain
    domain_obj = db.execute(
        select(DomainDB).where(func.lower(DomainDB.domain_name) == domain_part)
    ).scalar_one_or_none()

    empty_response = {
        "html": "",
        "inject_on_replies": bool(domain_obj and domain_obj.inject_on_replies),
        "trim_whitespace": bool(domain_obj.trim_whitespace) if domain_obj else True,
        "strip_device_signatures": bool(domain_obj.strip_device_signatures) if domain_obj else False,
    }
    if domain_obj is None or not domain_obj.is_active:
        return empty_response

    override = db.execute(
        select(UserOverrideDB).where(func.lower(UserOverrideDB.user_email) == authenticated_user)
    ).scalar_one_or_none()
    if override is not None and override.is_disabled:
        return empty_response

    profile = request.profile
    selected_from_variant = False
    selected_from_group = False
    selected_group_id = None
    selected_group_signature = None
    raw_template = ""
    selected_signature = None

    mode_candidate = (request.mode or "").strip().upper()
    explicit_mode = mode_candidate in {"AUTO", "PROFESSIONAL", "INTERNAL", "NONE"}
    if request.mode and not explicit_mode:
        raise HTTPException(status_code=400, detail="Unsupported signature mode")

    marker_requested = request.signature_name is not None or request.signature_code is not None
    if marker_requested and not explicit_mode:
        marker_name = (request.signature_name or "").strip()
        marker_code = (request.signature_code or "").strip().casefold()
        expected_code = hashlib.sha256(marker_name.casefold().encode("utf-8")).hexdigest()[:4]
        if (
            not marker_name
            or len(marker_name) > 255
            or len(marker_code) != 4
            or not hmac.compare_digest(marker_code, expected_code)
        ):
            return empty_response
        matching = db.execute(
            select(SignatureDB).where(
                SignatureDB.is_active.is_(True),
                func.lower(SignatureDB.name) == marker_name.casefold(),
            )
        ).scalars().all()
        assigned = set(db.execute(
            select(SignatureDomainDB.signature_id).where(
                SignatureDomainDB.domain_id == domain_obj.id
            )
        ).scalars().all())
        all_assigned = set(db.execute(select(SignatureDomainDB.signature_id)).scalars().all())
        domain_matches = [item for item in matching if item.id in assigned]
        global_matches = [item for item in matching if item.id not in all_assigned]
        candidates = domain_matches if domain_matches else global_matches
        if len(candidates) != 1:
            return empty_response
        selected_signature = candidates[0]
        latest = db.execute(
            select(SignatureVersionDB)
            .where(SignatureVersionDB.signature_id == selected_signature.id)
            .order_by(SignatureVersionDB.version_number.desc())
            .limit(1)
        ).scalar_one_or_none()
        if latest is None:
            raise HTTPException(status_code=503, detail="Signature version metadata is missing")
        raw_template = read_signature(selected_signature, latest)
    elif override is not None and domain_obj.allow_overrides and not explicit_mode:
        raw_template = override.html_content
    else:
        mode = None
        if explicit_mode:
            mode = resolve_mode(mode_candidate)
        else:
            context = RuleContext(
                sender=envelope_sender,
                domain=domain_part,
                groups=frozenset(profile.groups),
                claims=profile.claims,
                attributes=profile.attributes,
            )
            matched_rule = evaluate_rules(load_signature_rules(db), context)
            if matched_rule and matched_rule.action == "GROUP":
                if not matched_rule.group_id or not profile.principal_id:
                    return empty_response
                selected_signature = select_and_record_group_signature(
                    db, profile.principal_id, matched_rule.group_id, domain_obj.id
                )
                if selected_signature is None:
                    return empty_response
                selected_group_id = matched_rule.group_id
                selected_group_signature = selected_signature
                latest = latest_version(db, selected_signature.id)
                if latest is None:
                    raise HTTPException(status_code=503, detail="Signature version metadata is missing")
                raw_template = read_signature(selected_signature, latest)
                selected_from_group = True
            else:
                try:
                    mode = resolve_mode(matched_rule.action if matched_rule else None)
                except ValueError:
                    raise HTTPException(status_code=500, detail="Stored signature rule has an invalid action")
                signature_id = matched_rule.signature_id if matched_rule else None

        if mode == SignatureMode.NONE:
            return empty_response
        if mode is not None:
            variant = select_and_record_variant(
                db,
                sender_email=authenticated_user,
                pool_name=mode.value,
                domain_id=domain_obj.id,
                signature_id=signature_id if not explicit_mode else None,
            )
            if variant is not None:
                selected_signature = db.get(SignatureDB, variant.signature_id)
                if selected_signature is None:
                    raise HTTPException(status_code=503, detail="Selected signature metadata is missing")
                latest = latest_version(db, selected_signature.id)
                if latest is None:
                    raise HTTPException(status_code=503, detail="Signature version metadata is missing")
                raw_template = read_signature(selected_signature, latest)
                selected_from_variant = True
            elif mode == SignatureMode.AUTO and signature_id is None:
                raw_template = domain_obj.template_html
            else:
                return empty_response

    if not raw_template:
        return empty_response

    profile_values = {
        "first_name": profile.first_name or authenticated_user.split("@")[0].capitalize(),
        "last_name": profile.last_name,
        "title": profile.title,
        "phone": profile.phone,
        "email": envelope_sender,
        "domain_name": domain_part,
    }
    try:
        raw_template = sanitize_signature_html(raw_template)
        raw_template, group_slots = prepare_group_placeholders(raw_template)
        group_values = {}
        for slug, template_key in group_slots.items():
            group = db.execute(
                select(SignatureGroupDB).where(
                    SignatureGroupDB.slug == slug,
                    SignatureGroupDB.is_active.is_(True),
                )
            ).scalar_one_or_none()
            selected = None
            if group is not None and profile.principal_id:
                if group.id == selected_group_id:
                    selected = selected_group_signature
                else:
                    selected = select_and_record_group_signature(
                        db, profile.principal_id, group.id, domain_obj.id
                    )
                    if selected is not None:
                        selected_from_group = True
            if selected is None:
                group_values[template_key] = Markup("")
                continue
            nested = latest_version(db, selected.id)
            if nested is None:
                raise HTTPException(status_code=503, detail="Group signature version metadata is missing")
            nested_html = sanitize_signature_html(read_signature(selected, nested))
            nested_html = strip_group_placeholders(nested_html)
            group_values[template_key] = Markup(
                render_signature_template(nested_html, profile_values)
            )
        rendered = render_signature_template(raw_template, {**profile_values, **group_values})
    except TemplateError:
        raise HTTPException(status_code=422, detail="Invalid signature template")
    except (SignatureStorageError, ValueError, TypeError):
        logger.exception("Signature asset or template could not be processed")
        raise HTTPException(status_code=503, detail="Signature asset unavailable")

    if selected_from_variant or selected_from_group:
        db.commit()

    return {
        "html": rendered,
        "inject_on_replies": domain_obj.inject_on_replies,
        "trim_whitespace": domain_obj.trim_whitespace,
        "strip_device_signatures": domain_obj.strip_device_signatures,
    }


def latest_version(db: Session, signature_id: str):
    return db.execute(
        select(SignatureVersionDB)
        .where(SignatureVersionDB.signature_id == signature_id)
        .order_by(SignatureVersionDB.version_number.desc())
        .limit(1)
    ).scalar_one_or_none()


def read_signature(signature: SignatureDB, version: SignatureVersionDB) -> str:
    try:
        return signature_file_store.read(
            signature.name, uuid.UUID(hex=version.id), version.sha256
        )
    except (SignatureStorageError, ValueError, TypeError) as exc:
        logger.exception("Signature asset integrity/read failure")
        raise HTTPException(status_code=503, detail="Signature asset unavailable") from exc
