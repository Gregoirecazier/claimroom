"""Create the core private synthetic-claim schema."""

from pathlib import Path

from alembic import op


revision = "20260925_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = Path(__file__).parents[1] / "sql" / "core_schema.sql"
    for statement in schema.read_text(encoding="utf-8").split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    # Downgrade removes claim and audit data; run only against a disposable DB.
    op.execute("alter table if exists public.cases drop constraint if exists cases_current_draft_case_fk")
    for table in (
        "audit_events",
        "actions",
        "approvals",
        "drafts",
        "analysis_runs",
        "provider_results",
        "evidence",
        "cases",
    ):
        op.execute(f"drop table if exists public.{table}")
