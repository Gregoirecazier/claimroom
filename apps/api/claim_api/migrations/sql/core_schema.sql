-- Core storage for synthetic cases. Supabase supplies auth.users and application
-- tables are private to the server-side API and have no browser-role policies.

create table if not exists public.cases (
    id uuid primary key default gen_random_uuid(),
    created_by_user_id uuid not null references auth.users(id) on delete restrict,
    scenario_id text not null check (length(btrim(scenario_id)) > 0),
    synthetic boolean not null default true check (synthetic),
    status text not null default 'collecting'
        check (status in ('collecting', 'review_ready', 'approved', 'registered', 'sent')),
    state_version bigint not null default 1 check (state_version >= 0),
    content_revision bigint not null default 1 check (content_revision >= 0),
    intake_json jsonb not null check (jsonb_typeof(intake_json) = 'object'),
    current_draft_id uuid,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.evidence (
    id uuid primary key default gen_random_uuid(),
    case_id uuid not null references public.cases(id) on delete restrict,
    storage_path text not null unique,
    kind text not null,
    source_kind text not null,
    mode text not null check (mode in ('mock', 'live')),
    mime_type text not null,
    byte_size bigint not null check (byte_size >= 0),
    client_sha256 text,
    checksum_status text not null check (checksum_status in ('client_declared', 'verified')),
    received_at timestamptz not null default now()
);

create table if not exists public.provider_results (
    id uuid primary key default gen_random_uuid(),
    case_id uuid not null references public.cases(id) on delete restrict,
    provider text not null,
    mode text not null check (mode in ('mock', 'live')),
    status text not null check (status in ('matched', 'no_match', 'ambiguous', 'unavailable', 'error')),
    query_hash text not null,
    source_version text not null,
    payload_json jsonb not null default '{}'::jsonb check (jsonb_typeof(payload_json) = 'object'),
    reason text,
    retrieved_at timestamptz not null default now(),
    unique (case_id, provider, mode, query_hash, source_version),
    unique (case_id, id)
);

create table if not exists public.analysis_runs (
    id uuid primary key default gen_random_uuid(),
    case_id uuid not null references public.cases(id) on delete restrict,
    input_content_revision bigint not null check (input_content_revision >= 0),
    method_version text not null,
    status text not null check (status in ('running', 'ready', 'stale', 'failed')),
    input_json jsonb not null check (jsonb_typeof(input_json) = 'object'),
    output_json jsonb check (output_json is null or jsonb_typeof(output_json) = 'object'),
    error_code text,
    started_at timestamptz not null default now(),
    finished_at timestamptz
);

create table if not exists public.drafts (
    id uuid primary key default gen_random_uuid(),
    case_id uuid not null references public.cases(id) on delete restrict,
    analysis_run_id uuid references public.analysis_runs(id) on delete restrict,
    parent_draft_id uuid references public.drafts(id) on delete restrict,
    version integer not null check (version > 0),
    content_revision bigint not null check (content_revision >= 0),
    recipient_json jsonb check (recipient_json is null or jsonb_typeof(recipient_json) = 'object'),
    amount_minor bigint check (amount_minor is null or amount_minor >= 0),
    currency text check (currency is null or currency ~ '^[A-Z]{3}$'),
    body text not null,
    attachment_ids jsonb not null default '[]'::jsonb check (jsonb_typeof(attachment_ids) = 'array'),
    sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz not null default now(),
    unique (case_id, version),
    unique (case_id, id)
);

alter table public.cases
    add constraint cases_current_draft_case_fk
    foreign key (id, current_draft_id)
    references public.drafts(case_id, id)
    on delete restrict
    deferrable initially deferred;

create table if not exists public.approvals (
    id uuid primary key default gen_random_uuid(),
    case_id uuid not null references public.cases(id) on delete restrict,
    draft_id uuid not null,
    actor_user_id uuid not null references auth.users(id) on delete restrict,
    draft_sha256 text not null check (draft_sha256 ~ '^[0-9a-f]{64}$'),
    approved_content_revision bigint not null check (approved_content_revision >= 0),
    approved_at timestamptz not null default now(),
    superseded_at timestamptz,
    foreign key (case_id, draft_id) references public.drafts(case_id, id) on delete restrict
);

create unique index if not exists approvals_one_current_per_case
    on public.approvals(case_id) where superseded_at is null;

create table if not exists public.actions (
    id uuid primary key default gen_random_uuid(),
    case_id uuid not null references public.cases(id) on delete restrict,
    approval_id uuid not null references public.approvals(id) on delete restrict,
    draft_id uuid not null,
    registration_action_id uuid references public.actions(id) on delete restrict,
    kind text not null check (kind in ('registration', 'send')),
    mode text not null default 'mock' check (mode = 'mock'),
    status text not null check (status in ('confirmed', 'unknown', 'failed')),
    idempotency_key text not null check (length(btrim(idempotency_key)) > 0),
    payload_hash text not null check (payload_hash ~ '^[0-9a-f]{64}$'),
    reference text,
    created_at timestamptz not null default now(),
    foreign key (case_id, draft_id) references public.drafts(case_id, id) on delete restrict,
    unique (case_id, kind, idempotency_key)
);

create table if not exists public.audit_events (
    id uuid primary key default gen_random_uuid(),
    case_id uuid not null references public.cases(id) on delete restrict,
    actor_user_id uuid references auth.users(id) on delete restrict,
    event_type text not null,
    state_version_before bigint not null check (state_version_before >= 0),
    state_version_after bigint not null check (state_version_after >= state_version_before),
    content_revision_before bigint not null check (content_revision_before >= 0),
    content_revision_after bigint not null check (content_revision_after >= content_revision_before),
    metadata_json jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata_json) = 'object'),
    occurred_at timestamptz not null default now()
);

create index if not exists cases_created_by_updated_idx
    on public.cases(created_by_user_id, updated_at desc, id desc);
create index if not exists evidence_case_received_idx
    on public.evidence(case_id, received_at);
create index if not exists provider_results_case_retrieved_idx
    on public.provider_results(case_id, retrieved_at desc);
create index if not exists analysis_runs_case_started_idx
    on public.analysis_runs(case_id, started_at desc);
create index if not exists drafts_case_version_idx
    on public.drafts(case_id, version desc);
create index if not exists approvals_case_approved_idx
    on public.approvals(case_id, approved_at desc);
create index if not exists actions_case_created_idx
    on public.actions(case_id, created_at);
create index if not exists audit_events_case_occurred_idx
    on public.audit_events(case_id, occurred_at);

-- No anon/authenticated policies are added. The API uses a server-only role
-- after verifying the Supabase JWT, and browser clients access none of these rows.
alter table public.cases enable row level security;
alter table public.evidence enable row level security;
alter table public.provider_results enable row level security;
alter table public.analysis_runs enable row level security;
alter table public.drafts enable row level security;
alter table public.approvals enable row level security;
alter table public.actions enable row level security;
alter table public.audit_events enable row level security;
