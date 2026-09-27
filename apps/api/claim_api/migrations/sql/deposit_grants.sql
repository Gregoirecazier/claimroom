-- Deposit links are private server-side grants. The raw bearer token is never persisted here.
alter table public.case_messages
    add constraint case_messages_case_id_id_unique unique (case_id, id);

create table public.deposit_grants (
    id uuid primary key default gen_random_uuid(),
    case_id uuid not null references public.cases(id) on delete restrict,
    token_sha256 text not null unique check (token_sha256 ~ '^[0-9a-f]{64}$'),
    capabilities text[] not null check (
        cardinality(capabilities) > 0
        and capabilities <@ array[
            'read_summary', 'correct_intake', 'upload_evidence', 'attach_demo_media'
        ]::text[]
    ),
    content_revision bigint not null check (content_revision > 0),
    grant_version bigint not null default 1 check (grant_version > 0),
    expires_at timestamptz not null,
    revoked_at timestamptz,
    message_id uuid unique,
    created_at timestamptz not null default now(),
    check (expires_at > created_at),
    check (revoked_at is null or revoked_at >= created_at),
    foreign key (case_id, message_id)
        references public.case_messages(case_id, id) on delete restrict
);

create index deposit_grants_case_created_idx
    on public.deposit_grants(case_id, created_at desc);
create index deposit_grants_unassociated_expiry_idx
    on public.deposit_grants(expires_at)
    where message_id is null and revoked_at is null;

alter table public.deposit_grants enable row level security;
revoke all on public.deposit_grants from public;
