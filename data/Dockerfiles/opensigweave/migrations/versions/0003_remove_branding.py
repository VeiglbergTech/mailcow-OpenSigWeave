"""Remove standalone-style branding from the Mailcow integration."""
from alembic import op
import sqlalchemy as sa

revision = "0003_remove_branding"
down_revision = "0002_branding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "osw_branding_settings" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("osw_branding_settings")


def downgrade() -> None:
    if "osw_branding_settings" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "osw_branding_settings",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("app_name", sa.String(length=120), server_default="OpenSigWeave", nullable=False),
            sa.Column("app_color", sa.String(length=7), server_default="#ff7043", nullable=False),
            sa.Column("logo_png", sa.LargeBinary(), nullable=True),
            sa.Column("favicon_data", sa.LargeBinary(), nullable=True),
            sa.Column("favicon_mime", sa.String(length=64), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
