"""Add short insured sessions, correction provenance and demo media links."""

from alembic import op

revision = "20260925_insured_portal"
down_revision = "20260925_review_package"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        create table public.deposit_sessions (
            id uuid primary key default gen_random_uuid(),
            grant_id uuid not null references public.deposit_grants(id) on delete restrict,
            token_sha256 text not null unique check (token_sha256 ~ '^[0-9a-f]{64}$'),
            expires_at timestamptz not null,
            created_at timestamptz not null default now(),
            check (expires_at > created_at)
        );
        create index deposit_sessions_grant_idx on public.deposit_sessions(grant_id, expires_at);
        alter table public.deposit_sessions enable row level security;
        revoke all on public.deposit_sessions from public;
    """)
    op.execute("""
        create table public.insured_corrections (
            id uuid primary key default gen_random_uuid(),
            case_id uuid not null references public.cases(id) on delete restrict,
            grant_id uuid not null references public.deposit_grants(id) on delete restrict,
            session_id uuid not null references public.deposit_sessions(id) on delete restrict,
            field_name text not null,
            previous_value_json jsonb not null,
            new_value_json jsonb not null,
            reason text,
            content_revision bigint not null check (content_revision > 0),
            created_at timestamptz not null default now()
        );
        create index insured_corrections_case_idx on public.insured_corrections(case_id, created_at);
        alter table public.insured_corrections enable row level security;
        revoke all on public.insured_corrections from public;
    """)
    op.execute("""
        create table public.deposit_demo_media (
            case_id uuid not null references public.cases(id) on delete restrict,
            media_key text not null,
            evidence_id uuid not null unique,
            attached_by_grant_id uuid not null references public.deposit_grants(id) on delete restrict,
            attached_at timestamptz not null default now(),
            primary key (case_id, media_key),
            foreign key (case_id, evidence_id) references public.evidence(case_id, id) on delete restrict
        );
        alter table public.deposit_demo_media enable row level security;
        revoke all on public.deposit_demo_media from public;
    """)
    op.execute("""
        alter table public.evidence_upload_intents
            add column source_kind text not null default 'handler_upload'
            check (source_kind in ('handler_upload', 'insured_upload'));
        alter table public.evidence_upload_intents
            add column deposit_grant_id uuid references public.deposit_grants(id) on delete restrict;
        alter table public.evidence_upload_intents
            add constraint insured_intent_grant_check check (
                (source_kind = 'insured_upload') = (deposit_grant_id is not null)
            );
    """)


def downgrade() -> None:
    op.execute("alter table public.evidence_upload_intents drop column deposit_grant_id")
    op.execute("alter table public.evidence_upload_intents drop column source_kind")
    op.execute("drop table public.deposit_demo_media")
    op.execute("drop table public.insured_corrections")
    op.execute("drop table public.deposit_sessions")
