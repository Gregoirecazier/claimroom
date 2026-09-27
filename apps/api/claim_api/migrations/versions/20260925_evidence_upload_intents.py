"""Bind signed uploads to authorized case metadata."""

from pathlib import Path

from alembic import op


revision = "20260925_evidence"
down_revision = "20260925_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = Path(__file__).parents[1] / "sql" / "evidence_upload_intents.sql"
    for statement in schema.read_text(encoding="utf-8").split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute("drop table if exists public.evidence_upload_intents")
