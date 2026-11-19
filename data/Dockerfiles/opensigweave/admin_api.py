"""Private Mailcow administration API for OpenSigWeave."""
import hmac
import os
import uuid
import re

from fastapi import APIRouter, Depends, Header, HTTPException
from jinja2 import TemplateError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import create_database_engine, create_session_factory
from models import (
    DomainDB, SignatureDB, SignatureDomainDB, SignatureVersionDB,
    SignatureVariantDB, SignatureGroupDB, SignatureGroupDomainDB,
    SignatureGroupMemberDB, SignatureRuleDB, SignatureRuleConditionDB,
)
from core.slug import slugify
from signature_renderer import prepare_group_placeholders, render_signature_template
from signature_sanitizer import sanitize_signature_html
from signature_storage import MAX_SIGNATURE_BYTES, SignatureFileStore, SignatureStorageError

KEY = os.environ.get("ADMIN_API_KEY", "")
if len(KEY) < 32:
    raise RuntimeError("ADMIN_API_KEY must contain at least 32 characters")
SessionLocal = create_session_factory(create_database_engine())
storage = SignatureFileStore(os.environ.get("SIGNATURE_STORAGE_PATH", "/var/lib/opensigweave"))
router = APIRouter(prefix="/admin", tags=["private-admin"])
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def get_db():
    with SessionLocal() as session:
        yield session


def require_key(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    if not hmac.compare_digest(x_admin_key or "", KEY):
        raise HTTPException(status_code=401, detail="Unauthorized")


class DomainInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain_name: str = Field(min_length=3, max_length=253)
    is_active: bool = True
    allow_overrides: bool = True
    inject_on_replies: bool = False
    trim_whitespace: bool = True
    strip_device_signatures: bool = False


class SignatureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    html_content: str = Field(max_length=65536)
    mode: str = Field(default="AUTO", pattern="^(AUTO|PROFESSIONAL|INTERNAL)$")
    domain_ids: list[int] = Field(default_factory=list, max_length=256)
    is_active: bool = True
    weight: int = Field(default=1, ge=1, le=100000)
    variant_enabled: bool = True


class SignatureUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=255)
    html_content: str | None = Field(default=None, max_length=65536)
    mode: str | None = Field(default=None, pattern="^(AUTO|PROFESSIONAL|INTERNAL)$")
    domain_ids: list[int] | None = Field(default=None, max_length=256)
    is_active: bool | None = None
    weight: int | None = Field(default=None, ge=1, le=100000)
    variant_enabled: bool | None = None


class SignaturePreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    html_content: str = Field(min_length=1, max_length=65536)
    domain_id: int | None = None


class GroupMemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signature_id: str = Field(min_length=32, max_length=32)
    weight: int = Field(default=1, ge=1, le=100000)
    is_enabled: bool = True


class GroupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    domain_ids: list[int] = Field(default_factory=list, max_length=256)
    members: list[GroupMemberInput] = Field(default_factory=list, max_length=1000)
    is_active: bool = True


class RuleConditionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern="^(sender_equals|domain_equals|group_contains|claim_equals|attribute_equals)$")
    key: str = Field(default="", max_length=255)
    value: str = Field(min_length=1, max_length=2048)


class RuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    priority: int = Field(default=0, ge=-100000, le=100000)
    action_mode: str = Field(pattern="^(AUTO|PROFESSIONAL|INTERNAL|NONE|GROUP)$")
    signature_id: str | None = Field(default=None, min_length=32, max_length=32)
    group_id: str | None = Field(default=None, min_length=32, max_length=32)
    is_enabled: bool = True
    conditions: list[RuleConditionInput] = Field(default_factory=list, max_length=100)


def domain_json(row):
    return {"id": row.id, "domain_name": row.domain_name, "is_active": row.is_active,
            "allow_overrides": row.allow_overrides, "inject_on_replies": row.inject_on_replies,
            "trim_whitespace": row.trim_whitespace, "strip_device_signatures": row.strip_device_signatures}


def sanitize_admin_html(html_content: str) -> str:
    sanitized = sanitize_signature_html(html_content)
    if not sanitized.strip():
        raise HTTPException(status_code=422, detail="Signature HTML is empty after sanitization")
    if len(sanitized.encode("utf-8")) > MAX_SIGNATURE_BYTES:
        raise HTTPException(status_code=422, detail="Signature HTML exceeds the maximum allowed size")
    return sanitized


def version_json(version):
    return {
        "id": version.id,
        "version_number": version.version_number,
        "sha256": version.sha256,
        "size_bytes": version.size_bytes,
        "created_by": version.created_by,
        "created_at": version.created_at.isoformat() if version.created_at else None,
    }


def create_admin_version(db: Session, signature: SignatureDB, html_content: str, created_by: str | None):
    sanitized = sanitize_admin_html(html_content)
    latest = db.execute(
        select(SignatureVersionDB)
        .where(SignatureVersionDB.signature_id == signature.id)
        .order_by(SignatureVersionDB.version_number.desc())
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()
    version_number = (latest.version_number if latest else 0) + 1
    version_id = uuid.uuid4()
    stored = None
    try:
        stored = storage.write(version_id, signature.name, sanitized)
        version = SignatureVersionDB(
            id=version_id.hex,
            signature_id=signature.id,
            version_number=version_number,
            relative_path=stored.relative_path,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            created_by=created_by,
        )
        db.add(version)
        db.commit()
        db.refresh(version)
        return version
    except Exception:
        db.rollback()
        if stored is not None:
            try:
                storage.delete(signature.name, version_id)
                if latest is not None:
                    storage.restore_current(signature.name, uuid.UUID(hex=latest.id), latest.sha256)
            except Exception:
                pass
        raise


def signature_json(db, row):
    domains = db.execute(select(DomainDB.domain_name).join(
        SignatureDomainDB, SignatureDomainDB.domain_id == DomainDB.id
    ).where(SignatureDomainDB.signature_id == row.id)).scalars().all()
    version = db.execute(select(SignatureVersionDB).where(
        SignatureVersionDB.signature_id == row.id
    ).order_by(SignatureVersionDB.version_number.desc()).limit(1)).scalar_one_or_none()
    variant = db.execute(select(SignatureVariantDB).where(
        SignatureVariantDB.signature_id == row.id,
        SignatureVariantDB.pool_name == row.mode
    ).limit(1)).scalar_one_or_none()
    html = ""
    if version:
        try:
            html = storage.read(row.name, uuid.UUID(hex=version.id), version.sha256)
        except (SignatureStorageError, ValueError):
            raise HTTPException(status_code=503, detail="Signature asset unavailable")
    return {"id": row.id, "name": row.name, "mode": row.mode, "is_active": row.is_active,
            "scope": row.scope, "domains": domains, "html_content": html,
            "version": version.version_number if version else 0,
            "weight": variant.weight if variant else 1,
            "variant_enabled": variant.is_enabled if variant else False}


@router.get("/catalog", dependencies=[Depends(require_key)])
def get_catalog(db: Session = Depends(get_db)):
    domains = db.execute(select(DomainDB).order_by(DomainDB.domain_name)).scalars().all()
    signatures = db.execute(select(SignatureDB).order_by(SignatureDB.name)).scalars().all()
    return {"domains": [domain_json(d) for d in domains],
            "signatures": [signature_json(db, s) for s in signatures]}


@router.post("/domains", dependencies=[Depends(require_key)])
def add_domain(payload: DomainInput, db: Session = Depends(get_db)):
    name = payload.domain_name.strip().rstrip(".").casefold()
    if not DOMAIN_RE.fullmatch(name):
        raise HTTPException(status_code=422, detail="Invalid domain")
    if db.execute(select(DomainDB).where(DomainDB.domain_name == name)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Domain already exists")
    row = DomainDB(**{**payload.model_dump(), "domain_name": name})
    db.add(row)
    db.commit()
    db.refresh(row)
    return domain_json(row)


@router.put("/domains/{domain_id}", dependencies=[Depends(require_key)])
def edit_domain(domain_id: int, payload: DomainInput, db: Session = Depends(get_db)):
    row = db.get(DomainDB, domain_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Domain not found")
    name = payload.domain_name.strip().rstrip(".").casefold()
    if not DOMAIN_RE.fullmatch(name):
        raise HTTPException(status_code=422, detail="Invalid domain")
    duplicate = db.execute(select(DomainDB).where(DomainDB.domain_name == name, DomainDB.id != domain_id)).scalar_one_or_none()
    if duplicate:
        raise HTTPException(status_code=409, detail="Domain already exists")
    for key, value in payload.model_dump().items():
        setattr(row, key, name if key == "domain_name" else value)
    db.commit()
    db.refresh(row)
    return domain_json(row)


@router.delete("/domains/{domain_id}", dependencies=[Depends(require_key)])
def remove_domain(domain_id: int, db: Session = Depends(get_db)):
    row = db.get(DomainDB, domain_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Domain not found")
    signature_reference = db.execute(select(SignatureDomainDB.signature_id).where(
        SignatureDomainDB.domain_id == domain_id
    )).first()
    legacy_signature_reference = db.execute(select(SignatureDB.id).where(
        SignatureDB.domain_id == domain_id
    )).first()
    group_reference = db.execute(select(SignatureGroupDomainDB.group_id).where(
        SignatureGroupDomainDB.domain_id == domain_id
    )).first()
    if signature_reference or legacy_signature_reference or group_reference:
        raise HTTPException(status_code=409, detail="Domain is still assigned to signatures or groups")
    db.delete(row)
    db.commit()
    return {"deleted": domain_id}


@router.post("/signatures", dependencies=[Depends(require_key)])
def add_signature(payload: SignatureInput, db: Session = Depends(get_db)):
    name = payload.name.strip()
    version_id = uuid.uuid4()
    signature_id = uuid.uuid4().hex
    try:
        storage.ensure_name_available(name)
        html = sanitize_signature_html(payload.html_content)
        stored = storage.write(version_id, name, html)
    except (SignatureStorageError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        domains = db.execute(select(DomainDB).where(DomainDB.id.in_(payload.domain_ids))).scalars().all() if payload.domain_ids else []
        if len(domains) != len(set(payload.domain_ids)):
            raise HTTPException(status_code=422, detail="Unknown domain ID")
        row = SignatureDB(id=signature_id, name=name, mode=payload.mode, is_active=payload.is_active,
                           scope="DOMAIN" if domains else "GLOBAL", domain_id=domains[0].id if domains else None)
        db.add(row)
        db.flush()
        for domain in domains:
            db.add(SignatureDomainDB(signature_id=signature_id, domain_id=domain.id))
        db.add(SignatureVersionDB(id=version_id.hex, signature_id=signature_id, version_number=1,
                                  relative_path=stored.relative_path, sha256=stored.sha256,
                                  size_bytes=stored.size_bytes, created_by="mailcow-admin"))
        db.add(SignatureVariantDB(id=uuid.uuid4().hex, signature_id=signature_id,
                                  pool_name=payload.mode, weight=payload.weight,
                                  is_enabled=payload.variant_enabled))
        db.commit()
        db.refresh(row)
        return signature_json(db, row)
    except Exception:
        db.rollback()
        storage.delete(name, version_id)
        raise


@router.put("/signatures/{signature_id}", dependencies=[Depends(require_key)])
def edit_signature(signature_id: str, payload: SignatureUpdate, db: Session = Depends(get_db)):
    row = db.get(SignatureDB, signature_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Signature not found")
    old_name = row.name
    new_name = (payload.name or old_name).strip()
    if new_name != old_name and db.execute(select(SignatureDB).where(
        SignatureDB.name == new_name, SignatureDB.id != signature_id
    )).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Signature name already exists")
    renamed = False
    version_id = None
    try:
        old_pool = row.mode
        if new_name != old_name:
            storage.rename_signature(old_name, new_name)
            renamed = True
        if payload.html_content is not None:
            latest = db.execute(select(SignatureVersionDB).where(
                SignatureVersionDB.signature_id == signature_id
            ).order_by(SignatureVersionDB.version_number.desc()).limit(1)).scalar_one_or_none()
            version_id = uuid.uuid4()
            stored = storage.write(version_id, new_name, sanitize_signature_html(payload.html_content))
            db.add(SignatureVersionDB(id=version_id.hex, signature_id=signature_id,
                version_number=(latest.version_number if latest else 0) + 1,
                relative_path=stored.relative_path, sha256=stored.sha256,
                size_bytes=stored.size_bytes, created_by="mailcow-admin"))
        if payload.name is not None:
            row.name = new_name
        if payload.mode is not None:
            row.mode = payload.mode
        if payload.is_active is not None:
            row.is_active = payload.is_active
        if payload.mode is not None or payload.weight is not None or payload.variant_enabled is not None:
            new_pool = payload.mode or old_pool
            variant = db.execute(select(SignatureVariantDB).where(
                SignatureVariantDB.signature_id == signature_id,
                SignatureVariantDB.pool_name == old_pool
            ).order_by(SignatureVariantDB.id).limit(1)).scalar_one_or_none()
            if variant is None:
                variant = SignatureVariantDB(id=uuid.uuid4().hex, signature_id=signature_id,
                                             pool_name=new_pool, weight=payload.weight or 1,
                                             is_enabled=True if payload.variant_enabled is None else payload.variant_enabled)
                db.add(variant)
            else:
                variant.pool_name = new_pool
                if payload.weight is not None:
                    variant.weight = payload.weight
                if payload.variant_enabled is not None:
                    variant.is_enabled = payload.variant_enabled
        if payload.domain_ids is not None:
            domains = db.execute(select(DomainDB).where(DomainDB.id.in_(payload.domain_ids))).scalars().all() if payload.domain_ids else []
            if len(domains) != len(set(payload.domain_ids)):
                raise HTTPException(status_code=422, detail="Unknown domain ID")
            db.query(SignatureDomainDB).filter(SignatureDomainDB.signature_id == signature_id).delete(synchronize_session=False)
            for domain in domains:
                db.add(SignatureDomainDB(signature_id=signature_id, domain_id=domain.id))
            row.scope = "DOMAIN" if domains else "GLOBAL"
            row.domain_id = domains[0].id if domains else None
        db.commit()
        db.refresh(row)
        return signature_json(db, row)
    except Exception:
        db.rollback()
        if version_id is not None:
            storage.delete(new_name, version_id)
        if renamed:
            storage.rename_signature(new_name, old_name)
        raise


@router.delete("/signatures/{signature_id}", dependencies=[Depends(require_key)])
def remove_signature(signature_id: str, db: Session = Depends(get_db)):
    row = db.get(SignatureDB, signature_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Signature not found")
    name = row.name
    versions = db.execute(select(SignatureVersionDB).where(SignatureVersionDB.signature_id == signature_id)).scalars().all()
    db.delete(row)
    db.commit()
    for version in versions:
        try:
            storage.delete(name, uuid.UUID(hex=version.id))
        except SignatureStorageError:
            pass
    return {"deleted": signature_id}


class _GroupDomainLink:
    pass


@router.get("/groups", dependencies=[Depends(require_key)])
def get_groups(db: Session = Depends(get_db)):
    rows = db.execute(select(SignatureGroupDB).order_by(SignatureGroupDB.name)).scalars().all()
    result = []
    for row in rows:
        members = db.execute(select(SignatureGroupMemberDB).where(
            SignatureGroupMemberDB.group_id == row.id)).scalars().all()
        domains = db.execute(select(DomainDB.domain_name).join(
            SignatureGroupDomainDB, SignatureGroupDomainDB.domain_id == DomainDB.id
        ).where(SignatureGroupDomainDB.group_id == row.id)).scalars().all()
        result.append({"id": row.id, "name": row.name, "slug": row.slug,
                       "is_active": row.is_active, "domains": domains,
                       "members": [{"signature_id": m.signature_id, "weight": m.weight,
                                    "is_enabled": m.is_enabled} for m in members]})
    return result


@router.post("/groups", dependencies=[Depends(require_key)])
def add_group(payload: GroupInput, db: Session = Depends(get_db)):
    name = payload.name.strip()
    slug = slugify(name)[:64]
    if not slug:
        raise HTTPException(status_code=422, detail="Group name must contain letters or numbers")
    if db.execute(select(SignatureGroupDB).where(SignatureGroupDB.slug == slug)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Group name already exists")
    ids = [m.signature_id for m in payload.members]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=422, detail="Duplicate group member")
    if ids:
        found = set(db.execute(select(SignatureDB.id).where(SignatureDB.id.in_(ids))).scalars().all())
        if found != set(ids):
            raise HTTPException(status_code=422, detail="Unknown signature ID")
    if payload.domain_ids:
        found_domains = set(db.execute(select(DomainDB.id).where(DomainDB.id.in_(payload.domain_ids))).scalars().all())
        if found_domains != set(payload.domain_ids):
            raise HTTPException(status_code=422, detail="Unknown domain ID")
    group_id = uuid.uuid4().hex
    row = SignatureGroupDB(id=group_id, name=name, slug=slug, is_active=payload.is_active)
    db.add(row)
    for member in payload.members:
        db.add(SignatureGroupMemberDB(group_id=group_id, signature_id=member.signature_id,
                                      weight=member.weight, is_enabled=member.is_enabled))
    for domain_id in set(payload.domain_ids):
        db.add(SignatureGroupDomainDB(group_id=group_id, domain_id=domain_id))
    db.commit()
    return {"id": group_id, "name": name, "slug": slug}


@router.put("/groups/{group_id}", dependencies=[Depends(require_key)])
def edit_group(group_id: str, payload: GroupInput, db: Session = Depends(get_db)):
    row = db.get(SignatureGroupDB, group_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Group not found")
    name = payload.name.strip()
    slug = slugify(name)[:64]
    if not slug:
        raise HTTPException(status_code=422, detail="Invalid group name")
    duplicate = db.execute(select(SignatureGroupDB).where(
        SignatureGroupDB.slug == slug, SignatureGroupDB.id != group_id
    )).scalar_one_or_none()
    if duplicate:
        raise HTTPException(status_code=409, detail="Group name already exists")
    ids = [m.signature_id for m in payload.members]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=422, detail="Duplicate group member")
    found = set(db.execute(select(SignatureDB.id).where(SignatureDB.id.in_(ids))).scalars().all()) if ids else set()
    if found != set(ids):
        raise HTTPException(status_code=422, detail="Unknown signature ID")
    found_domains = set(db.execute(select(DomainDB.id).where(DomainDB.id.in_(payload.domain_ids))).scalars().all()) if payload.domain_ids else set()
    if found_domains != set(payload.domain_ids):
        raise HTTPException(status_code=422, detail="Unknown domain ID")
    row.name, row.slug, row.is_active = name, slug, payload.is_active
    db.query(SignatureGroupMemberDB).filter(SignatureGroupMemberDB.group_id == group_id).delete(synchronize_session=False)
    db.query(SignatureGroupDomainDB).filter(SignatureGroupDomainDB.group_id == group_id).delete(synchronize_session=False)
    for member in payload.members:
        db.add(SignatureGroupMemberDB(group_id=group_id, signature_id=member.signature_id,
                                      weight=member.weight, is_enabled=member.is_enabled))
    for domain_id in set(payload.domain_ids):
        db.add(SignatureGroupDomainDB(group_id=group_id, domain_id=domain_id))
    db.commit()
    return {"id": group_id, "name": name, "slug": slug}


@router.delete("/groups/{group_id}", dependencies=[Depends(require_key)])
def remove_group(group_id: str, db: Session = Depends(get_db)):
    row = db.get(SignatureGroupDB, group_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Group not found")
    db.delete(row)
    db.commit()
    return {"deleted": group_id}


@router.get("/rules", dependencies=[Depends(require_key)])
def get_rules(db: Session = Depends(get_db)):
    rows = db.execute(select(SignatureRuleDB).order_by(SignatureRuleDB.priority.desc(), SignatureRuleDB.id)).scalars().all()
    result = []
    for row in rows:
        conditions = db.execute(select(SignatureRuleConditionDB).where(
            SignatureRuleConditionDB.rule_id == row.id)).scalars().all()
        result.append({"id": row.id, "name": row.name, "priority": row.priority,
                       "action_mode": row.action_mode, "signature_id": row.signature_id,
                       "group_id": row.group_id, "is_enabled": row.is_enabled,
                       "conditions": [{"kind": c.kind, "key": c.key, "value": c.value}
                                      for c in conditions]})
    return result


@router.post("/rules", dependencies=[Depends(require_key)])
def add_rule(payload: RuleInput, db: Session = Depends(get_db)):
    if payload.action_mode == "GROUP" and not payload.group_id:
        raise HTTPException(status_code=422, detail="GROUP rule requires a group")
    if payload.action_mode != "GROUP" and payload.group_id:
        raise HTTPException(status_code=422, detail="Only GROUP rules can reference a group")
    if payload.signature_id and db.get(SignatureDB, payload.signature_id) is None:
        raise HTTPException(status_code=422, detail="Unknown signature ID")
    if payload.group_id and db.get(SignatureGroupDB, payload.group_id) is None:
        raise HTTPException(status_code=422, detail="Unknown group ID")
    rule_id = uuid.uuid4().hex
    row = SignatureRuleDB(id=rule_id, name=payload.name.strip(), priority=payload.priority,
                          action_mode=payload.action_mode, signature_id=payload.signature_id,
                          group_id=payload.group_id, is_enabled=payload.is_enabled)
    db.add(row)
    db.flush()
    for condition in payload.conditions:
        db.add(SignatureRuleConditionDB(rule_id=rule_id, kind=condition.kind,
                                        key=condition.key, value=condition.value))
    db.commit()
    return {"id": rule_id, "name": row.name}


@router.put("/rules/{rule_id}", dependencies=[Depends(require_key)])
def edit_rule(rule_id: str, payload: RuleInput, db: Session = Depends(get_db)):
    row = db.get(SignatureRuleDB, rule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    if payload.action_mode == "GROUP" and not payload.group_id:
        raise HTTPException(status_code=422, detail="GROUP rule requires a group")
    if payload.action_mode != "GROUP" and payload.group_id:
        raise HTTPException(status_code=422, detail="Only GROUP rules can reference a group")
    if payload.signature_id and db.get(SignatureDB, payload.signature_id) is None:
        raise HTTPException(status_code=422, detail="Unknown signature ID")
    if payload.group_id and db.get(SignatureGroupDB, payload.group_id) is None:
        raise HTTPException(status_code=422, detail="Unknown group ID")
    row.name, row.priority, row.action_mode = payload.name.strip(), payload.priority, payload.action_mode
    row.signature_id, row.group_id, row.is_enabled = payload.signature_id, payload.group_id, payload.is_enabled
    db.query(SignatureRuleConditionDB).filter(SignatureRuleConditionDB.rule_id == rule_id).delete(synchronize_session=False)
    for condition in payload.conditions:
        db.add(SignatureRuleConditionDB(rule_id=rule_id, kind=condition.kind,
                                        key=condition.key, value=condition.value))
    db.commit()
    return {"id": rule_id, "name": row.name}


@router.delete("/rules/{rule_id}", dependencies=[Depends(require_key)])
def remove_rule(rule_id: str, db: Session = Depends(get_db)):
    row = db.get(SignatureRuleDB, rule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.delete(row)
    db.commit()
    return {"deleted": rule_id}


@router.post("/preview", dependencies=[Depends(require_key)])
def preview_signature(payload: SignaturePreviewInput, db: Session = Depends(get_db)):
    domain_name = "example.test"
    if payload.domain_id is not None:
        domain = db.get(DomainDB, payload.domain_id)
        if domain is None:
            raise HTTPException(status_code=422, detail="Unknown domain ID")
        domain_name = domain.domain_name
    try:
        raw_template = sanitize_admin_html(payload.html_content)
        raw_template, group_slots = prepare_group_placeholders(raw_template)
        group_values = {template_key: "" for template_key in group_slots.values()}
        rendered = render_signature_template(raw_template, {
            "first_name": "Max",
            "last_name": "Mustermann",
            "title": "IT-Abteilung",
            "phone": "+49 30 123456",
            "email": "max@" + domain_name,
            "domain_name": domain_name,
            **group_values,
        })
    except TemplateError as exc:
        raise HTTPException(status_code=422, detail="Invalid signature template") from exc
    return {"html": rendered, "preview": True}


@router.get("/signatures/{signature_id}/versions", dependencies=[Depends(require_key)])
def list_signature_versions(signature_id: str, db: Session = Depends(get_db)):
    signature = db.get(SignatureDB, signature_id)
    if signature is None:
        raise HTTPException(status_code=404, detail="Signature not found")
    versions = db.execute(
        select(SignatureVersionDB)
        .where(SignatureVersionDB.signature_id == signature_id)
        .order_by(SignatureVersionDB.version_number.desc())
    ).scalars().all()
    return {"versions": [version_json(version) for version in versions]}


@router.post(
    "/signatures/{signature_id}/versions/{version_id}/restore",
    dependencies=[Depends(require_key)],
)
def restore_signature_version(signature_id: str, version_id: str, db: Session = Depends(get_db)):
    signature = db.get(SignatureDB, signature_id)
    if signature is None:
        raise HTTPException(status_code=404, detail="Signature not found")
    historical = db.get(SignatureVersionDB, version_id)
    if historical is None or historical.signature_id != signature_id:
        raise HTTPException(status_code=404, detail="Signature version not found")
    try:
        html_content = storage.read(signature.name, uuid.UUID(hex=historical.id), historical.sha256)
    except (SignatureStorageError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="Signature asset unavailable") from exc
    restored = create_admin_version(db, signature, html_content, "mailcow-admin-restore")
    return version_json(restored)
