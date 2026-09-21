import sqlalchemy as sa
from alembic import op

revision = "0005_digest_publication_state"
down_revision = "0004_recovery_proof"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "digests", sa.Column("status", sa.String(), server_default="pending", nullable=False)
    )
    op.add_column("digests", sa.Column("last_error", sa.String(), nullable=True))
    op.add_column("digests", sa.Column("last_attempt_at", sa.DateTime(), nullable=True))
    op.add_column("digests", sa.Column("published_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE digests SET status = 'published' WHERE notion_page_id IS NOT NULL")
    with op.batch_alter_table("digests") as batch_op:
        batch_op.create_check_constraint(
            "ck_digests_status", "status IN ('pending', 'failed', 'uncertain', 'published')"
        )


def downgrade() -> None:
    with op.batch_alter_table("digests") as batch_op:
        batch_op.drop_constraint("ck_digests_status", type_="check")
    op.drop_column("digests", "published_at")
    op.drop_column("digests", "last_attempt_at")
    op.drop_column("digests", "last_error")
    op.drop_column("digests", "status")
