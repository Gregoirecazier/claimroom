"""Track simulated CCTV requests without contacting camera controllers."""

from alembic import op

revision = "20260925_camera_requests"
down_revision = "20260925_deposit_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        create table public.case_camera_requests (
            id uuid primary key default gen_random_uuid(),
            case_id uuid not null references public.cases(id) on delete restrict,
            candidate_id text not null check (length(btrim(candidate_id)) > 0),
            candidate_label text not null,
            source text not null,
            controller text,
            recipient text,
            scope text not null check (length(btrim(scope)) >= 10),
            reason text not null check (length(btrim(reason)) >= 10),
            status text not null default 'draft' check (status in
                ('draft', 'requested', 'waiting', 'denied', 'unknown_owner',
                 'received', 'unavailable', 'timed_out')),
            mode text not null default 'mock' check (mode = 'mock'),
            fixture_event_id text,
            evidence_id uuid unique references public.evidence(id) on delete restrict,
            created_by_user_id uuid not null references auth.users(id) on delete restrict,
            approved_by_user_id uuid references auth.users(id) on delete restrict,
            status_actor_user_id uuid references auth.users(id) on delete restrict,
            created_at timestamptz not null default now(),
            approved_at timestamptz,
            requested_at timestamptz,
            status_changed_at timestamptz,
            received_at timestamptz,
            unique (case_id, candidate_id),
            check (status <> 'received' or (evidence_id is not null and received_at is not null)),
            check (status = 'draft' or status in ('unknown_owner', 'unavailable')
                   or approved_by_user_id is not null)
        )
    """)
    op.execute("create index case_camera_requests_case_idx on public.case_camera_requests(case_id, created_at)")
    op.execute("alter table public.case_camera_requests enable row level security")


def downgrade() -> None:
    op.execute("drop table public.case_camera_requests")
