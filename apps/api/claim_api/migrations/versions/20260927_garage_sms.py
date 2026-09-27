"""Durable garage reply jobs and case-scoped SMS routing."""

from alembic import op

revision = "20260927_garage_sms"
down_revision = "20260926_reference_data"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(r"""create table public.garage_sms_settings (
        case_id uuid primary key references public.cases(id) on delete restrict,
        recipient text not null check (recipient ~ '^\+[1-9][0-9]{1,14}$'),
        enabled boolean not null default false,
        location_text text,
        origin_json jsonb,
        updated_at timestamptz not null default now()
    )""")
    op.execute("create index garage_sms_recipient on public.garage_sms_settings(recipient) where enabled")
    op.execute(r"""create table public.garage_reply_jobs (
        id uuid primary key default gen_random_uuid(),
        case_id uuid not null references public.cases(id) on delete restrict,
        trigger_id text not null unique,
        mode text not null check (mode in ('mock','live')),
        status text not null default 'queued' check (status in
            ('queued','processing','submitting','done','failed','unknown','needs_location','cancelled')),
        recipient text not null check (recipient ~ '^\+[1-9][0-9]{1,14}$'),
        location_text text,
        origin_json jsonb,
        media_json jsonb not null default '[]'::jsonb,
        result_json jsonb,
        message_id uuid references public.case_messages(id) on delete restrict,
        error_code text,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now()
    )""")
    op.execute("create index garage_jobs_pending on public.garage_reply_jobs(created_at) where status='queued'")
    op.execute("create index garage_jobs_case on public.garage_reply_jobs(case_id,created_at desc)")
    for table in ("garage_sms_settings", "garage_reply_jobs"):
        op.execute(f"alter table public.{table} enable row level security")
        op.execute(f"revoke all on public.{table} from public")
        # Supabase client roles do not exist on plain PostgreSQL used by CI.
        op.execute(f"""do $$ declare r text; begin
            for r in select rolname from pg_catalog.pg_roles where rolname in ('anon','authenticated')
            loop execute format('revoke all on public.{table} from %I',r); end loop;
        end $$""")


def downgrade():
    op.execute("drop table public.garage_reply_jobs")
    op.execute("drop table public.garage_sms_settings")
