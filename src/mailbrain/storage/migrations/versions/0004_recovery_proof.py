import sqlalchemy as sa
from alembic import op

revision = "0004_recovery_proof"
down_revision = "0003_durable_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mutations", sa.Column("reconciled_at", sa.DateTime(), nullable=True))
    op.add_column(
        "label_creation_intents", sa.Column("reconciled_at", sa.DateTime(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("label_creation_intents", "reconciled_at")
    op.drop_column("mutations", "reconciled_at")
