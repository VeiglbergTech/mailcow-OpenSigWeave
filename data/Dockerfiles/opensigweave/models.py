"""Mailcow-mode SQLAlchemy models.

Only OpenSigWeave-owned application data is stored here. The model set
intentionally excludes standalone LDAP/OIDC provider registries and caches:
Mailcow remains the identity source in this deployment.
"""
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from database import Base


class DomainDB(Base):
    """OpenSigWeave policy for one canonical, active Mailcow domain.

    domain_name is a logical reference to Mailcow's authoritative domain.domain
    value. The engine verifies existence and active state against that table
    before returning a signature; this table never creates Mailcow domains.
    """
    __tablename__ = "osw_domains"
    id = Column(Integer, primary_key=True, index=True)
    domain_name = Column(String(253), unique=True, index=True, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    allow_overrides = Column(Boolean, nullable=False, default=True)
    inject_on_replies = Column(Boolean, nullable=False, default=False)
    trim_whitespace = Column(Boolean, nullable=False, default=True)
    strip_device_signatures = Column(Boolean, nullable=False, default=False)
    template_html = Column(Text, nullable=False, default="")
    template_html_source = Column(Text, nullable=True)


class UserOverrideDB(Base):
    __tablename__ = "osw_user_overrides"
    id = Column(Integer, primary_key=True, index=True)
    user_email = Column(String(254), unique=True, index=True, nullable=False)
    html_content = Column(Text, nullable=False, default="")
    html_content_source = Column(Text, nullable=True)
    is_disabled = Column(Boolean, nullable=False, default=False)


class SignatureDB(Base):
    __tablename__ = "osw_signatures"
    id = Column(String(32), primary_key=True)
    name = Column(String(255), nullable=False)
    domain_id = Column(
        Integer, ForeignKey("osw_domains.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    scope = Column(String(32), nullable=False, default="DOMAIN")
    mode = Column(String(32), nullable=False, default="AUTO")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SignatureDomainDB(Base):
    __tablename__ = "osw_signature_domains"
    signature_id = Column(
        String(32), ForeignKey("osw_signatures.id", ondelete="CASCADE"),
        primary_key=True,
    )
    domain_id = Column(
        Integer, ForeignKey("osw_domains.id", ondelete="CASCADE"),
        primary_key=True,
    )


class SignatureVersionDB(Base):
    __tablename__ = "osw_signature_versions"
    id = Column(String(32), primary_key=True)
    signature_id = Column(
        String(32), ForeignKey("osw_signatures.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    version_number = Column(Integer, nullable=False)
    relative_path = Column(String(255), nullable=False, unique=True)
    sha256 = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    created_by = Column(String(254), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint(
            "signature_id", "version_number",
            name="uq_osw_signature_version_number",
        ),
    )


class SignatureGroupDB(Base):
    __tablename__ = "osw_signature_groups"
    id = Column(String(32), primary_key=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(64), nullable=False, unique=True, index=True)
    selection_policy = Column(
        String(40), nullable=False, default="WEIGHTED_RANDOM_NO_REPEAT"
    )
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SignatureGroupDomainDB(Base):
    __tablename__ = "osw_signature_group_domains"
    group_id = Column(
        String(32), ForeignKey("osw_signature_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    domain_id = Column(
        Integer, ForeignKey("osw_domains.id", ondelete="CASCADE"),
        primary_key=True,
    )


class SignatureGroupMemberDB(Base):
    __tablename__ = "osw_signature_group_members"
    group_id = Column(
        String(32), ForeignKey("osw_signature_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    signature_id = Column(
        String(32), ForeignKey("osw_signatures.id", ondelete="CASCADE"),
        primary_key=True,
    )
    weight = Column(Integer, nullable=False, default=1)
    is_enabled = Column(Boolean, nullable=False, default=True)
    __table_args__ = (
        CheckConstraint("weight > 0", name="ck_osw_group_member_weight_positive"),
    )


class GroupSelectionStateDB(Base):
    __tablename__ = "osw_group_selection_state"
    principal_id = Column(String(320), primary_key=True)
    group_id = Column(
        String(32), ForeignKey("osw_signature_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    last_signature_id = Column(
        String(32), ForeignKey("osw_signatures.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )


class SignatureVariantDB(Base):
    __tablename__ = "osw_signature_variants"
    id = Column(String(32), primary_key=True)
    signature_id = Column(
        String(32), ForeignKey("osw_signatures.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    pool_name = Column(String(32), nullable=False, default="AUTO")
    weight = Column(Integer, nullable=False, default=1)
    is_enabled = Column(Boolean, nullable=False, default=True)
    __table_args__ = (
        CheckConstraint("weight > 0", name="ck_osw_variant_weight_positive"),
    )


class SelectionStateDB(Base):
    __tablename__ = "osw_selection_state"
    id = Column(Integer, primary_key=True)
    sender_email = Column(String(254), nullable=False)
    pool_name = Column(String(32), nullable=False)
    last_variant_id = Column(
        String(32), ForeignKey("osw_signature_variants.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )
    __table_args__ = (
        UniqueConstraint(
            "sender_email", "pool_name", name="uq_osw_selection_sender_pool"
        ),
    )


class SignatureRuleDB(Base):
    __tablename__ = "osw_signature_rules"
    id = Column(String(32), primary_key=True)
    name = Column(String(255), nullable=False)
    priority = Column(Integer, nullable=False, default=0)
    action_mode = Column(String(32), nullable=False, default="AUTO")
    signature_id = Column(
        String(32), ForeignKey("osw_signatures.id", ondelete="SET NULL"),
        nullable=True,
    )
    group_id = Column(
        String(32), ForeignKey("osw_signature_groups.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    is_enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SignatureRuleConditionDB(Base):
    __tablename__ = "osw_signature_rule_conditions"
    id = Column(Integer, primary_key=True)
    rule_id = Column(
        String(32), ForeignKey("osw_signature_rules.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    kind = Column(String(64), nullable=False)
    key = Column(String(255), nullable=False, default="")
    value = Column(Text, nullable=False)


class RateLimitBucketDB(Base):
    __tablename__ = "osw_rate_limit_buckets"
    bucket_key = Column(String(64), primary_key=True)
    window_start = Column(Integer, nullable=False)
    request_count = Column(Integer, nullable=False)
