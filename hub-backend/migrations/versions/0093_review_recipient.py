"""Store an explicitly designated delivery reviewer separately from client-editable routing."""
from alembic import op
import sqlalchemy as sa

revision = "0093_review_recipient"
down_revision = "0092_task_text_storage"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("task", sa.Column("review_recipient_user_id", sa.String(36), nullable=True))


def downgrade():
    op.drop_column("task", "review_recipient_user_id")
