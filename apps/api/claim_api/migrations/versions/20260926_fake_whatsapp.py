"""Store private fake WhatsApp replies and their evidence attachments."""

from alembic import op

revision = "20260926_fake_whatsapp"
down_revision = "20260926_dust_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""create table public.case_whatsapp_inbound (
        id uuid primary key default gen_random_uuid(),
        case_id uuid references public.cases(id) on delete restrict,
        provider_message_id text not null unique,
        sender text not null check (sender ~ '^\\+[1-9][0-9]{1,14}$'),
        body text not null default '',
        media_count integer not null default 0 check (media_count between 0 and 10),
        evidence_id uuid references public.evidence(id) on delete restrict,
        received_at timestamptz not null default now()
    )""")
    op.execute("create index case_whatsapp_inbound_case_idx on public.case_whatsapp_inbound(case_id, received_at desc)")
    op.execute("create index case_whatsapp_inbound_evidence_idx on public.case_whatsapp_inbound(evidence_id) where evidence_id is not null")
    op.execute("alter table public.case_whatsapp_inbound enable row level security")
    op.execute("revoke all on public.case_whatsapp_inbound from public")


def downgrade() -> None:
    op.execute("drop table public.case_whatsapp_inbound")
