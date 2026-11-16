"""Private Mailcow administration API for OpenSigWeave."""
import hmac
import os
import uuid
import re

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import create_database_engine, create_session_factory
from models import DomainDB, SignatureDB, SignatureDomainDB, SignatureVersionDB
from signature_sanitizer import sanitize_signature_html
from signature_storage import SignatureFileStore, SignatureStorageError

KEY = os.environ.get("ENGINE_API_KEY", "")
if len(KEY) < 32:
    raise RuntimeError("ENGINE_API_KEY must contain at least 32 characters")
SessionLocal = create_session_factory(create_database_engine())
storage = SignatureFileStore(os.environ.get("SIGNATURE_STORAGE_PATH", "/var/lib/opensigweave"))
router = APIRouter(prefix="/admin", tags=["private-admin"])
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def get_db():
    with SessionLocal() as session:
        yield session


def require_key(x_engine_key: str | None = Header(default=None, alias="X-Engine-Key")):
    if not hmac.compare_digest(x_engine_key or "", KEY):
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


class SignatureUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=255)
    html_content: str | None = Field(default=None, max_length=65536)
    mode: str | None = Field(default=None, pattern="^(AUTO|PROFESSIONAL|INTERNAL)$")
    domain_ids: list[int] | None = Field(default=None, max_length=256)
    is_active: bool | None = None


def domain_json(row):
    return {"id": row.id, "domain_name": row.domain_name, "is_active": row.is_active,
            "allow_overrides": row.allow_overrides, "inject_on_replies": row.inject_on_replies,
            "trim_whitespace": row.trim_whitespace, "strip_device_signatures": row.strip_device_signatures}


def signature_json(db, row):
    domains = db.execute(select(DomainDB.domain_name).join(
        SignatureDomainDB, SignatureDomainDB.domain_id == DomainDB.id
    ).where(SignatureDomainDB.signature_id == row.id)).scalars().all()
    version = db.execute(select(SignatureVersionDB).where(
        SignatureVersionDB.signature_id == row.id
    ).order_by(SignatureVersionDB.version_number.desc()).limit(1)).scalar_one_or_none()
    html = ""
    if version:
        try:
            html = storage.read(row.name, uuid.UUID(hex=version.id), version.sha256)
        except (SignatureStorageError, ValueError):
            raise HTTPException(status_code=503, detail="Signature asset unavailable")
    return {"id": row.id, "name": row.name, "mode": row.mode, "is_active": row.is_active,
            "scope": row.scope, "domains": domains, "html_content": html,
            "version": version.version_number if version else 0}


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
