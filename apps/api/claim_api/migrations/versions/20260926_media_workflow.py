"""Durable media follow-up and reviewable external correspondence."""

from alembic import op

revision = "20260926_media_workflow"
down_revision = "20260926_fake_whatsapp"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""create table public.case_media_workflows (
        id uuid primary key default gen_random_uuid(),
        case_id uuid not null references public.cases(id) on delete cascade,
        content_revision bigint not null,
        status text not null default 'queued' check (status in
            ('queued','processing','waiting','ready','failed','needs_action','stale')),
        lease_id uuid, lease_until timestamptz,
        available_at timestamptz not null default now(),
        attempts integer not null default 0,
        generation integer not null default 1,
        analysis_run_id uuid references public.analysis_runs(id),
        garage_run_id uuid references public.dust_runs(id),
        insurance_matches jsonb not null default '[]',
        error_code text,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        unique (case_id, content_revision)
    );
    create index media_workflow_pending on public.case_media_workflows(available_at)
        where status in ('queued','waiting','processing');
    create table public.case_correspondence (
        id uuid primary key default gen_random_uuid(),
        case_id uuid not null references public.cases(id) on delete cascade,
        kind text not null check (kind in ('cctv','garage')),
        content_revision bigint not null,
        source_run_id uuid references public.dust_runs(id),
        recipient text not null default '',
        subject text not null,
        body text not null,
        source_url text,
        version integer not null default 1,
        status text not null default 'draft' check (status in ('draft','sending','sent','failed','unknown')),
        send_payload jsonb,
        send_started_at timestamptz,
        provider_message_id text,
        error_code text,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        unique (source_run_id)
    );
    create index correspondence_case on public.case_correspondence(case_id,created_at desc);
    """)
    for table in ("case_media_workflows", "case_correspondence"):
        op.execute(f"alter table public.{table} enable row level security")
        op.execute(f"revoke all on public.{table} from public")
        op.execute(f"""do $$ declare r text; begin
            for r in select rolname from pg_catalog.pg_roles where rolname in ('anon','authenticated')
            loop execute format('revoke all on public.{table} from %I',r); end loop;
        end $$""")


def downgrade():
    op.execute("drop table public.case_correspondence")
    op.execute("drop table public.case_media_workflows")
