"""Persist asynchronous, review-only Dust specialist runs."""

from alembic import op

revision = "20260926_dust_runs"
down_revision = "20260926_video_evidence"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""create table public.dust_runs (
        id uuid primary key,
        case_id uuid not null references public.cases(id) on delete cascade,
        agent text not null check (agent in ('legal','cctv','repair','garage','recovery')),
        agent_id text not null,
        workspace_id text not null,
        base_url text not null check (base_url in ('https://dust.tt','https://eu.dust.tt')),
        input_content_revision bigint not null check (input_content_revision > 0),
        idempotency_key uuid not null,
        request_hash text not null,
        gemini_analysis_run_id uuid references public.analysis_runs(id),
        conversation_id text,
        status text not null check (status in
            ('submitting','running','needs_action','ready','failed','submission_unknown','stale')),
        input_json jsonb not null,
        output jsonb,
        error_code text,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        unique (case_id, idempotency_key)
    )""")
    op.execute(
        "create index dust_runs_case_created on public.dust_runs(case_id, created_at desc)"
    )
    op.execute(
        """create unique index dust_runs_active_agent on public.dust_runs(case_id, agent)
        where status in ('submitting','running','needs_action')"""
    )
    op.execute("alter table public.dust_runs enable row level security")
    op.execute("revoke all on public.dust_runs from public")
    # Supabase defines these roles; vanilla PostgreSQL (including CI) does not.
    # Keep this check in SQL so offline migration rendering works as well.
    op.execute("""do $$
    declare client_role text;
    begin
        for client_role in
            select rolname from pg_catalog.pg_roles
            where rolname in ('anon', 'authenticated')
        loop
            execute format('revoke all on public.dust_runs from %I', client_role);
        end loop;
    end $$""")


def downgrade():
    op.execute("drop table public.dust_runs")
