"""Add case-scoped, hashed insured deposit grants."""

from pathlib import Path

from alembic import op

revision = "20260925_deposit_grants"
down_revision = "20260925_counterparty_lookup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = Path(__file__).parents[1] / "sql" / "deposit_grants.sql"
    for statement in schema.read_text(encoding="utf-8").split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    # Destructive: only use on a disposable database.
    op.execute("drop table public.deposit_grants")
    op.execute("alter table public.case_messages drop constraint case_messages_case_id_id_unique")
