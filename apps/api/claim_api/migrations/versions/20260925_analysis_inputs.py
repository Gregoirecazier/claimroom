"""Persist exact provider query fields for analysis source references."""

from alembic import op


revision = "20260925_analysis_inputs"
down_revision = "20260925_case_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table public.provider_results add column query_json jsonb not null default '{}'::jsonb")


def downgrade() -> None:
    op.execute("alter table public.provider_results drop column query_json")
