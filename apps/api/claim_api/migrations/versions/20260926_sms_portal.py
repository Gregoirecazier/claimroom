"""Private conversation opened by a case-scoped SMS deposit link."""

from alembic import op

revision = "20260926_sms_portal"
down_revision = "20260926_media_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""create table public.case_portal_messages (
        id uuid primary key default gen_random_uuid(),
        case_id uuid not null references public.cases(id) on delete restrict,
        grant_id uuid not null references public.deposit_grants(id) on delete restrict,
        sender text not null check (sender in ('manager', 'insured')),
        client_message_id uuid not null,
        body text not null check (length(btrim(body)) between 1 and 1600),
        created_at timestamptz not null default now(),
        unique (grant_id, sender, client_message_id)
    )""")
    op.execute("create index case_portal_messages_case_idx on public.case_portal_messages(case_id, created_at, id)")
    op.execute("create index case_portal_messages_grant_idx on public.case_portal_messages(grant_id, created_at, id)")
    op.execute("alter table public.case_portal_messages enable row level security")
    op.execute("revoke all on public.case_portal_messages from public")


def downgrade() -> None:
    op.execute("drop table public.case_portal_messages")
