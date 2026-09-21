import sqlalchemy as sa
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "labels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("gmail_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("gmail_id"),
    )
    op.create_table(
        "threads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("gmail_id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("gmail_id"),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("gmail_id", sa.String(), nullable=False),
        sa.Column("thread_gmail_id", sa.String(), nullable=True),
        sa.Column("sender", sa.String(), nullable=True),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("snippet", sa.String(), nullable=True),
        sa.Column("history_id", sa.String(), nullable=True),
        sa.Column("internal_date", sa.DateTime(), nullable=True),
        sa.Column("label_ids", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("gmail_id"),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("scanned", sa.Integer(), nullable=False),
        sa.Column("labeled", sa.Integer(), nullable=False),
        sa.Column("archived", sa.Integer(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "mutations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("message_gmail_id", sa.String(), nullable=False),
        sa.Column("labels_before", sa.String(), server_default="[]", nullable=False),
        sa.Column("labels_after", sa.String(), server_default="[]", nullable=False),
        sa.Column("archived_before", sa.Boolean(), nullable=True),
        sa.Column("archived_after", sa.Boolean(), nullable=True),
        sa.Column("read_before", sa.Boolean(), nullable=True),
        sa.Column("read_after", sa.Boolean(), nullable=True),
        sa.Column("applied", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "digests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("iso_week", sa.String(), nullable=False),
        sa.Column("notion_page_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("iso_week"),
    )
    op.create_table(
        "review_queue",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("message_gmail_id", sa.String(), nullable=False),
        sa.Column("suggested", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "sync_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("last_history_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("sync_state")
    op.drop_table("review_queue")
    op.drop_table("digests")
    op.drop_table("mutations")
    op.drop_table("runs")
    op.drop_table("messages")
    op.drop_table("threads")
    op.drop_table("labels")
