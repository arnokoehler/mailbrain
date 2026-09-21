import sqlalchemy as sa
from alembic import op

revision = "0003_durable_execution"
down_revision = "0002_scan_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_columns = (
        sa.Column("run_type", sa.String(), server_default="apply", nullable=False),
        sa.Column("status", sa.String(), server_default="legacy", nullable=False),
        sa.Column("scan_id", sa.Integer(), nullable=True),
        sa.Column("source_run_id", sa.Integer(), nullable=True),
        sa.Column("rules_hash", sa.String(), nullable=True),
        sa.Column("safety_hash", sa.String(), nullable=True),
        sa.Column("prepared_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("intended_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("applied_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("uncertain_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("conflict_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("abandoned_at", sa.DateTime(), nullable=True),
        sa.Column("abandonment_reason", sa.String(), nullable=True),
    )
    for column in run_columns:
        op.add_column("runs", column)
    with op.batch_alter_table("runs") as batch_op:
        batch_op.create_check_constraint("ck_runs_run_type", "run_type IN ('apply', 'rollback')")
        batch_op.create_check_constraint(
            "ck_runs_status",
            "status IN ('prepared', 'running', 'succeeded', 'partial', 'failed', "
            "'needs_review', 'legacy', 'abandoned')",
        )
        batch_op.create_foreign_key("fk_runs_scan_id_scan_runs", "scan_runs", ["scan_id"], ["id"])
        batch_op.create_foreign_key("fk_runs_source_run_id_runs", "runs", ["source_run_id"], ["id"])
        batch_op.create_index("ix_runs_scan_id", ["scan_id"])
        batch_op.create_index("ix_runs_source_run_id", ["source_run_id"])
        batch_op.create_index("ix_runs_status", ["status"])

    mutation_columns = (
        sa.Column("status", sa.String(), server_default="legacy", nullable=False),
        sa.Column("batch_id", sa.String(), nullable=True),
        sa.Column("original_mutation_id", sa.Integer(), nullable=True),
        sa.Column("matched_rule_ids", sa.String(), server_default="[]", nullable=False),
        sa.Column("gmail_label_ids_before", sa.String(), nullable=True),
        sa.Column("gmail_label_ids_add", sa.String(), nullable=True),
        sa.Column("gmail_label_ids_remove", sa.String(), nullable=True),
        sa.Column("prepared_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("abandoned_at", sa.DateTime(), nullable=True),
        sa.Column("abandonment_reason", sa.String(), nullable=True),
    )
    for column in mutation_columns:
        op.add_column("mutations", column)
    with op.batch_alter_table("mutations") as batch_op:
        batch_op.create_check_constraint(
            "ck_mutations_status",
            "status IN ('pending', 'in_flight', 'applied', 'failed', 'uncertain', "
            "'conflict', 'legacy', 'abandoned')",
        )
        batch_op.create_foreign_key(
            "fk_mutations_original_mutation_id_mutations",
            "mutations",
            ["original_mutation_id"],
            ["id"],
        )
        batch_op.create_index("ix_mutations_status", ["status"])
        batch_op.create_index("ix_mutations_batch_id", ["batch_id"])
        batch_op.create_index("ix_mutations_original_mutation_id", ["original_mutation_id"])

    op.create_table(
        "label_creation_intents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("label_name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), server_default="pending", nullable=False),
        sa.Column("gmail_label_id", sa.String(), nullable=True),
        sa.Column("prepared_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'in_flight', 'resolved', 'failed', 'uncertain', 'abandoned')",
            name="ck_label_creation_intents_status",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "label_name", name="uq_label_creation_intents_run_name"),
    )
    op.create_index(
        "ix_label_creation_intents_status", "label_creation_intents", ["status"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_label_creation_intents_status", table_name="label_creation_intents")
    op.drop_table("label_creation_intents")
    with op.batch_alter_table("mutations") as batch_op:
        batch_op.drop_index("ix_mutations_original_mutation_id")
        batch_op.drop_index("ix_mutations_batch_id")
        batch_op.drop_index("ix_mutations_status")
        batch_op.drop_constraint("fk_mutations_original_mutation_id_mutations", type_="foreignkey")
        batch_op.drop_constraint("ck_mutations_status", type_="check")
    for name in (
        "abandonment_reason",
        "abandoned_at",
        "error_message",
        "finished_at",
        "started_at",
        "prepared_at",
        "gmail_label_ids_remove",
        "gmail_label_ids_add",
        "gmail_label_ids_before",
        "matched_rule_ids",
        "original_mutation_id",
        "batch_id",
        "status",
    ):
        op.drop_column("mutations", name)
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_index("ix_runs_status")
        batch_op.drop_index("ix_runs_source_run_id")
        batch_op.drop_index("ix_runs_scan_id")
        batch_op.drop_constraint("fk_runs_source_run_id_runs", type_="foreignkey")
        batch_op.drop_constraint("fk_runs_scan_id_scan_runs", type_="foreignkey")
        batch_op.drop_constraint("ck_runs_status", type_="check")
        batch_op.drop_constraint("ck_runs_run_type", type_="check")
    for name in (
        "abandonment_reason",
        "abandoned_at",
        "conflict_count",
        "uncertain_count",
        "failed_count",
        "applied_count",
        "intended_count",
        "error_message",
        "prepared_at",
        "safety_hash",
        "rules_hash",
        "source_run_id",
        "scan_id",
        "status",
        "run_type",
    ):
        op.drop_column("runs", name)
