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
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import create_database_engine, create_session_factory
from models import (
    DomainDB, GroupSelectionStateDB, SignatureDB, SignatureDomainDB,
    SignatureGroupDB, SignatureGroupMemberDB, SignatureRuleDB,
    SignatureVariantDB, SignatureVersionDB, UserOverrideDB,
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
from signature_storage import MAX_SIGNATURE_BYTES, SignatureFileStore, SignatureStorageError

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
    labels = target_domain.split(".")
    if (
        len(target_domain) > 253
        or len(labels) < 2
        or any(
            not label
            or len(label) > 63
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
            for label in labels
        )
    ):
        raise HTTPException(status_code=400, detail="Invalid target domain")

    domain_part = target_domain

    # Mailcow owns the domain lifecycle. An OpenSigWeave policy row is valid
    # only while the canonical domain exists and is active in Mailcow itself.
    try:
        mailcow_domain = db.execute(
            text(
                "SELECT `active` FROM `domain` "
                "WHERE `domain` = :domain LIMIT 1"
            ),
            {"domain": domain_part},
        ).first()
    except SQLAlchemyError as exc:
        logger.exception("Could not verify target domain against Mailcow")
        raise HTTPException(
            status_code=503, detail="Mailcow domain directory unavailable"
        ) from exc

    if mailcow_domain is None or str(mailcow_domain[0]).strip().lower() not in {
        "1", "true", "yes"
    }:
        return {
            "html": "",
            "inject_on_replies": False,
            "trim_whitespace": True,
            "strip_device_signatures": False,
        }

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
    # Invalid mode values are ignored, matching the standalone engine contract;
    # normal rules and automatic selection remain the fallback.
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



class SignatureAdminCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    mode: str = Field(default="AUTO", max_length=32)
    html_content: str = Field(min_length=1, max_length=65536)
    created_by: str | None = Field(default=None, max_length=254)


class SignatureAdminVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    html_content: str = Field(min_length=1, max_length=65536)
    created_by: str | None = Field(default=None, max_length=254)


class SignatureAdminUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: bool


VALID_SIGNATURE_MODES = {"AUTO", "PROFESSIONAL", "INTERNAL", "NONE"}


def sanitize_admin_html(html_content: str) -> str:
    sanitized = sanitize_signature_html(html_content)
    if not sanitized.strip():
        raise HTTPException(status_code=422, detail="Signature HTML is empty after sanitization")
    if len(sanitized.encode("utf-8")) > MAX_SIGNATURE_BYTES:
        raise HTTPException(status_code=422, detail="Signature HTML exceeds the maximum allowed size")
    return sanitized


def normalize_admin_domain(value: str) -> str:
    domain = value.strip().rstrip(".").casefold()
    labels = domain.split(".")
    if (
        len(domain) > 253
        or len(labels) < 2
        or any(
            not label
            or len(label) > 63
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
            for label in labels
        )
    ):
        raise HTTPException(status_code=400, detail="Invalid domain")
    return domain


def require_active_mailcow_domain(db: Session, domain: str) -> None:
    try:
        row = db.execute(
            text("SELECT active FROM domain WHERE domain = :domain LIMIT 1"),
            {"domain": domain},
        ).first()
    except SQLAlchemyError as exc:
        logger.exception("Could not verify management domain against Mailcow")
        raise HTTPException(status_code=503, detail="Mailcow domain directory unavailable") from exc
    if row is None or str(row[0]).strip().lower() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=404, detail="Active Mailcow domain not found")


def get_or_create_domain_policy(db: Session, domain: str) -> DomainDB:
    domain_obj = db.execute(
        select(DomainDB).where(func.lower(DomainDB.domain_name) == domain)
    ).scalar_one_or_none()
    if domain_obj is None:
        domain_obj = DomainDB(domain_name=domain, is_active=True)
        db.add(domain_obj)
        db.flush()
    return domain_obj


def require_signature_in_domain(
    db: Session, domain_obj: DomainDB, signature_id: str
) -> SignatureDB:
    signature = db.get(SignatureDB, signature_id)
    if signature is None:
        raise HTTPException(status_code=404, detail="Signature not found")
    assigned = db.execute(
        select(SignatureDomainDB.signature_id).where(
            SignatureDomainDB.signature_id == signature_id,
            SignatureDomainDB.domain_id == domain_obj.id,
        )
    ).scalar_one_or_none()
    if assigned is None:
        raise HTTPException(status_code=404, detail="Signature not found for this domain")
    return signature


def signature_version_payload(version: SignatureVersionDB) -> dict[str, Any]:
    return {
        "id": version.id,
        "version_number": version.version_number,
        "sha256": version.sha256,
        "size_bytes": version.size_bytes,
        "created_by": version.created_by,
        "created_at": version.created_at.isoformat() if version.created_at else None,
    }


def create_signature_version(
    db: Session,
    signature: SignatureDB,
    html_content: str,
    created_by: str | None,
) -> SignatureVersionDB:
    sanitized = sanitize_admin_html(html_content)
    latest = db.execute(
        select(SignatureVersionDB)
        .where(SignatureVersionDB.signature_id == signature.id)
        .order_by(SignatureVersionDB.version_number.desc())
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()
    version_number = (latest.version_number if latest else 0) + 1
    version_uuid = uuid.uuid4()
    stored = None
    try:
        stored = signature_file_store.write(version_uuid, signature.name, sanitized)
        version = SignatureVersionDB(
            id=version_uuid.hex,
            signature_id=signature.id,
            version_number=version_number,
            relative_path=stored.relative_path,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            created_by=created_by,
        )
        db.add(version)
        db.commit()
        return version
    except Exception:
        db.rollback()
        if stored is not None:
            try:
                signature_file_store.delete(signature.name, version_uuid)
                if latest is not None:
                    signature_file_store.restore_current(
                        signature.name, uuid.UUID(hex=latest.id), latest.sha256
                    )
            except Exception:
                logger.exception("Could not roll back signature file after version failure")
        raise


@app.get(
    "/internal/admin/domains/{domain}/signatures",
    dependencies=[Depends(require_engine_key)],
)
def list_domain_signatures(domain: str, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store, private"
    domain = normalize_admin_domain(domain)
    require_active_mailcow_domain(db, domain)
    domain_obj = db.execute(
        select(DomainDB).where(func.lower(DomainDB.domain_name) == domain)
    ).scalar_one_or_none()
    if domain_obj is None:
        return {"domain": domain, "signatures": []}
    signature_ids = db.execute(
        select(SignatureDomainDB.signature_id).where(
            SignatureDomainDB.domain_id == domain_obj.id
        )
    ).scalars().all()
    if not signature_ids:
        return {"domain": domain, "signatures": []}
    signatures = db.execute(
        select(SignatureDB)
        .where(SignatureDB.id.in_(signature_ids))
        .order_by(SignatureDB.name)
    ).scalars().all()
    items = []
    for signature in signatures:
        latest = latest_version(db, signature.id)
        items.append({
            "id": signature.id,
            "name": signature.name,
            "mode": signature.mode,
            "is_active": signature.is_active,
            "latest_version": latest.version_number if latest else None,
        })
    return {"domain": domain, "signatures": items}


@app.post(
    "/internal/admin/domains/{domain}/signatures",
    status_code=201,
    dependencies=[Depends(require_engine_key)],
)
def create_domain_signature(
    domain: str,
    request: SignatureAdminCreateRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    domain = normalize_admin_domain(domain)
    require_active_mailcow_domain(db, domain)
    name = request.name.strip()
    mode = request.mode.strip().upper()
    if mode not in VALID_SIGNATURE_MODES:
        raise HTTPException(status_code=422, detail="Invalid signature mode")
    duplicate = db.execute(
        select(SignatureDB.id).where(func.lower(SignatureDB.name) == name.casefold()).limit(1)
    ).scalar_one_or_none()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="A signature with this name already exists")
    try:
        signature_file_store.ensure_name_available(name)
    except SignatureStorageError as exc:
        raise HTTPException(status_code=409, detail="A signature with this name already exists") from exc

    domain_obj = get_or_create_domain_policy(db, domain)
    signature_id = uuid.uuid4().hex
    signature = SignatureDB(
        id=signature_id,
        name=name,
        domain_id=domain_obj.id,
        scope="DOMAIN",
        mode=mode,
        is_active=True,
    )
    db.add(signature)
    db.flush()
    db.add(SignatureDomainDB(signature_id=signature_id, domain_id=domain_obj.id))
    version_uuid = uuid.uuid4()
    stored = None
    try:
        sanitized = sanitize_admin_html(request.html_content)
        stored = signature_file_store.write(version_uuid, name, sanitized)
        version = SignatureVersionDB(
            id=version_uuid.hex,
            signature_id=signature_id,
            version_number=1,
            relative_path=stored.relative_path,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            created_by=request.created_by,
        )
        db.add(version)
        db.add(SignatureVariantDB(
            id=uuid.uuid4().hex,
            signature_id=signature_id,
            pool_name=mode,
            weight=1,
            is_enabled=True,
        ))
        db.commit()
    except Exception:
        db.rollback()
        if stored is not None:
            try:
                signature_file_store.delete(name, version_uuid)
            except Exception:
                logger.exception("Could not remove signature files after create failure")
        raise
    return {
        "id": signature_id,
        "name": name,
        "domain": domain,
        "mode": mode,
        "is_active": True,
        "latest_version": 1,
    }


@app.get(
    "/internal/admin/domains/{domain}/signatures/{signature_id}",
    dependencies=[Depends(require_engine_key)],
)
def get_domain_signature(
    domain: str,
    signature_id: str,
    response: Response,
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    domain = normalize_admin_domain(domain)
    require_active_mailcow_domain(db, domain)
    domain_obj = db.execute(
        select(DomainDB).where(func.lower(DomainDB.domain_name) == domain)
    ).scalar_one_or_none()
    if domain_obj is None:
        raise HTTPException(status_code=404, detail="Signature not found")
    signature = require_signature_in_domain(db, domain_obj, signature_id)
    latest = latest_version(db, signature.id)
    if latest is None:
        raise HTTPException(status_code=503, detail="Signature version metadata is missing")
    return {
        "id": signature.id,
        "name": signature.name,
        "domain": domain,
        "mode": signature.mode,
        "is_active": signature.is_active,
        "latest_version": latest.version_number,
        "html_content": read_signature(signature, latest),
    }


@app.put(
    "/internal/admin/domains/{domain}/signatures/{signature_id}",
    dependencies=[Depends(require_engine_key)],
)
def update_domain_signature(
    domain: str,
    signature_id: str,
    request: SignatureAdminUpdateRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    domain = normalize_admin_domain(domain)
    require_active_mailcow_domain(db, domain)
    domain_obj = db.execute(
        select(DomainDB).where(func.lower(DomainDB.domain_name) == domain)
    ).scalar_one_or_none()
    if domain_obj is None:
        raise HTTPException(status_code=404, detail="Signature not found")
    signature = require_signature_in_domain(db, domain_obj, signature_id)
    signature.is_active = request.is_active
    db.commit()
    return {"id": signature.id, "is_active": signature.is_active}


@app.get(
    "/internal/admin/domains/{domain}/signatures/{signature_id}/versions",
    dependencies=[Depends(require_engine_key)],
)
def list_domain_signature_versions(
    domain: str,
    signature_id: str,
    response: Response,
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    domain = normalize_admin_domain(domain)
    require_active_mailcow_domain(db, domain)
    domain_obj = db.execute(
        select(DomainDB).where(func.lower(DomainDB.domain_name) == domain)
    ).scalar_one_or_none()
    if domain_obj is None:
        raise HTTPException(status_code=404, detail="Signature not found")
    signature = require_signature_in_domain(db, domain_obj, signature_id)
    versions = db.execute(
        select(SignatureVersionDB)
        .where(SignatureVersionDB.signature_id == signature.id)
        .order_by(SignatureVersionDB.version_number.desc())
    ).scalars().all()
    return {"versions": [signature_version_payload(item) for item in versions]}


@app.post(
    "/internal/admin/domains/{domain}/signatures/{signature_id}/versions",
    status_code=201,
    dependencies=[Depends(require_engine_key)],
)
def add_domain_signature_version(
    domain: str,
    signature_id: str,
    request: SignatureAdminVersionRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    domain = normalize_admin_domain(domain)
    require_active_mailcow_domain(db, domain)
    domain_obj = db.execute(
        select(DomainDB).where(func.lower(DomainDB.domain_name) == domain)
    ).scalar_one_or_none()
    if domain_obj is None:
        raise HTTPException(status_code=404, detail="Signature not found")
    signature = require_signature_in_domain(db, domain_obj, signature_id)
    version = create_signature_version(db, signature, request.html_content, request.created_by)
    return signature_version_payload(version)


@app.post(
    "/internal/admin/domains/{domain}/signatures/{signature_id}/restore/{version_id}",
    status_code=201,
    dependencies=[Depends(require_engine_key)],
)
def restore_domain_signature_version(
    domain: str,
    signature_id: str,
    version_id: str,
    response: Response,
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, private"
    domain = normalize_admin_domain(domain)
    require_active_mailcow_domain(db, domain)
    domain_obj = db.execute(
        select(DomainDB).where(func.lower(DomainDB.domain_name) == domain)
    ).scalar_one_or_none()
    if domain_obj is None:
        raise HTTPException(status_code=404, detail="Signature not found")
    signature = require_signature_in_domain(db, domain_obj, signature_id)
    historical = db.get(SignatureVersionDB, version_id)
    if historical is None or historical.signature_id != signature.id:
        raise HTTPException(status_code=404, detail="Signature version not found")
    html_content = read_signature(signature, historical)
    version = create_signature_version(db, signature, html_content, None)
    return signature_version_payload(version)
