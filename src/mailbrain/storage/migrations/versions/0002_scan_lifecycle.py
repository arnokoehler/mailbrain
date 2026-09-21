import sqlalchemy as sa
from alembic import op

revision = "0002_scan_lifecycle"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scan_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("query", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(), server_default="running", nullable=False),
        sa.Column("listed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("fetched_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'invalidated')",
            name="ck_scan_runs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column("messages", sa.Column("last_scan_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("messages") as batch_op:
        batch_op.create_foreign_key(
            "fk_messages_last_scan_id_scan_runs", "scan_runs", ["last_scan_id"], ["id"]
        )
        batch_op.create_index("ix_messages_last_scan_id", ["last_scan_id"])


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_index("ix_messages_last_scan_id")
        batch_op.drop_constraint("fk_messages_last_scan_id_scan_runs", type_="foreignkey")
        batch_op.drop_column("last_scan_id")
    op.drop_table("scan_runs")
