"""Add private follow-up messages independent of claim approval."""

from pathlib import Path

from alembic import op

revision = "20260925_case_messages"
down_revision = "20260925_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = Path(__file__).parents[1] / "sql" / "case_messages.sql"
    for statement in schema.read_text(encoding="utf-8").split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    # Destructive: removes message history. Use only on a disposable database.
    op.execute("drop table public.case_messages")
