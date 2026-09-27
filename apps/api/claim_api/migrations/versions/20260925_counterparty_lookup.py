"""Track the selected involved vehicle and current mock lookup results."""

from alembic import op

revision = "20260925_counterparty_lookup"
down_revision = "20260925_voice_intake"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        create table public.case_counterparty_lookups (
            case_id uuid primary key references public.cases(id) on delete restrict,
            plate_candidate text not null,
            country text not null,
            incident_date date not null,
            vehicle_track_id text not null,
            identification_status text not null check (identification_status in ('observed', 'human_confirmed', 'uncertain')),
            supporting_source_refs_json jsonb not null check (jsonb_typeof(supporting_source_refs_json) = 'array'),
            validation_reason text,
            validated_by_user_id uuid references auth.users(id) on delete restrict,
            validated_at timestamptz,
            insurance_result_id uuid not null,
            correspondent_result_id uuid,
            active boolean not null default true,
            updated_at timestamptz not null default now(),
            foreign key (case_id, insurance_result_id) references public.provider_results(case_id, id),
            foreign key (case_id, correspondent_result_id) references public.provider_results(case_id, id)
        );
        alter table public.case_counterparty_lookups enable row level security;
    """)


def downgrade() -> None:
    op.execute("drop table public.case_counterparty_lookups")
