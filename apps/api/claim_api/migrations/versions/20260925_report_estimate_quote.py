"""Persist signed report corrections, versioned estimates, and PDF quote associations."""

from alembic import op


revision = "20260925_report_estimate_quote"
down_revision = "20260925_g1_evidence_video"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table public.evidence_upload_intents add column filename text")
    op.execute("alter table public.evidence add column original_filename text")
    op.execute("""update public.evidence e set original_filename = i.filename
        from public.evidence_upload_intents i where i.finalized_evidence_id = e.id""")
    op.execute("alter table public.evidence add constraint evidence_case_id_id_unique unique (case_id, id)")
    op.execute("""create table public.report_line_edits (
        id uuid primary key default gen_random_uuid(),
        case_id uuid not null references public.cases(id) on delete restrict,
        line_id text not null check (length(btrim(line_id)) > 0),
        text text not null check (length(btrim(text)) > 0),
        uncertainty text,
        source_refs_json jsonb not null default '[]'::jsonb check (jsonb_typeof(source_refs_json) = 'array'),
        previous_text text not null,
        as_of_revision bigint not null check (as_of_revision > 0),
        actor_user_id uuid not null references auth.users(id) on delete restrict,
        created_at timestamptz not null default now(),
        unique (case_id, line_id, as_of_revision)
    )""")
    op.execute("create index report_line_edits_case_line_idx on public.report_line_edits(case_id, line_id, as_of_revision desc)")
    op.execute("alter table public.report_line_edits enable row level security")
    op.execute("""create table public.case_estimates (
        id uuid primary key default gen_random_uuid(),
        case_id uuid not null references public.cases(id) on delete restrict,
        version integer not null check (version > 0),
        line_items_json jsonb not null check (jsonb_typeof(line_items_json) = 'array'),
        total_minor bigint not null check (total_minor > 0),
        currency text not null check (currency = 'EUR'),
        tax_basis text not null check (tax_basis = 'TTC'),
        estimate_source text not null check (estimate_source in ('demo_fixture', 'handler', 'quote', 'agent_proposal')),
        source_refs_json jsonb not null default '[]'::jsonb check (jsonb_typeof(source_refs_json) = 'array'),
        created_by_user_id uuid references auth.users(id) on delete restrict,
        created_at timestamptz not null default now(),
        unique (case_id, version)
    )""")
    op.execute("create index case_estimates_case_version_idx on public.case_estimates(case_id, version desc)")
    op.execute("alter table public.case_estimates enable row level security")
    op.execute("""create table public.case_quotes (
        id uuid primary key default gen_random_uuid(),
        case_id uuid not null references public.cases(id) on delete restrict,
        version integer not null check (version > 0),
        evidence_id uuid,
        total_ttc_minor bigint,
        amount_source text,
        attached_estimate_version integer,
        created_by_user_id uuid not null references auth.users(id) on delete restrict,
        created_at timestamptz not null default now(),
        unique (case_id, version),
        foreign key (case_id, evidence_id) references public.evidence(case_id, id) on delete restrict,
        foreign key (case_id, attached_estimate_version) references public.case_estimates(case_id, version) on delete restrict,
        check ((evidence_id is null and total_ttc_minor is null and amount_source is null)
            or (evidence_id is not null and total_ttc_minor > 0
                and amount_source in ('handler_entered', 'demo_fixture')))
    )""")
    op.execute("create index case_quotes_case_version_idx on public.case_quotes(case_id, version desc)")
    op.execute("alter table public.case_quotes enable row level security")


def downgrade() -> None:
    op.execute("drop table public.case_quotes")
    op.execute("drop table public.case_estimates")
    op.execute("drop table public.report_line_edits")
    op.execute("alter table public.evidence drop constraint evidence_case_id_id_unique")
    op.execute("alter table public.evidence drop column original_filename")
    op.execute("alter table public.evidence_upload_intents drop column filename")
