"""Persist private chat messages and the current conversation step per case."""

from alembic import op

revision = "20260927_deposit_history"
down_revision = "20260927_garage_sms"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""create table public.deposit_chat_history (
        case_id uuid primary key references public.cases(id) on delete restrict,
        revision bigint not null check (revision > 0),
        state_json jsonb not null check (jsonb_typeof(state_json) = 'object'),
        updated_at timestamptz not null default now()
    )""")
    op.execute("alter table public.deposit_chat_history enable row level security")
    op.execute("revoke all on public.deposit_chat_history from public")
    op.execute("""do $$ declare r text; begin
        for r in select rolname from pg_catalog.pg_roles where rolname in ('anon','authenticated')
        loop execute format('revoke all on public.deposit_chat_history from %I',r); end loop;
    end $$""")


def downgrade():
    op.execute("drop table public.deposit_chat_history")
