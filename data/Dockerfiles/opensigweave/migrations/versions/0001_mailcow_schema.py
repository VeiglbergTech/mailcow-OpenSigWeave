"""Create the initial Mailcow-mode OpenSigWeave schema."""
from alembic import op

from database import Base
import models  # noqa: F401 - register metadata

revision = "0001_mailcow_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
