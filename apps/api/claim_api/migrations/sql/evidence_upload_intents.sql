-- Short-lived, server-owned records bind a signed Storage upload token to one
-- authenticated owner, case, immutable path, and declared object metadata.
create table if not exists public.evidence_upload_intents (
    id uuid primary key default gen_random_uuid(),
    case_id uuid not null references public.cases(id) on delete restrict,
    actor_user_id uuid not null references auth.users(id) on delete restrict,
    storage_path text not null unique,
    kind text not null check (kind in ('scene_photo', 'vehicle_photo', 'damage_photo', 'document', 'other')),
    mime_type text not null check (mime_type in ('image/jpeg', 'image/png', 'image/webp', 'application/pdf')),
    byte_size bigint not null check (byte_size > 0 and byte_size <= 5242880),
    client_sha256 text not null check (client_sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz not null default now(),
    expires_at timestamptz not null,
    finalized_evidence_id uuid references public.evidence(id) on delete restrict,
    finalized_at timestamptz,
    check (left(storage_path, length(case_id::text) + 1) = case_id::text || '/'),
    check (expires_at > created_at),
    check ((finalized_evidence_id is null) = (finalized_at is null))
);

create index if not exists evidence_upload_intents_case_created_idx
    on public.evidence_upload_intents(case_id, created_at desc);

alter table public.evidence_upload_intents enable row level security;
