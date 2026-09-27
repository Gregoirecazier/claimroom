-- Follow-up messages precede claim approval and are not claim-send actions.
create table public.case_messages (
    id uuid primary key default gen_random_uuid(),
    case_id uuid not null references public.cases(id) on delete restrict,
    channel text not null default 'sms' check (channel = 'sms'),
    mode text not null default 'mock' check (mode in ('mock', 'live')),
    recipient text not null check (recipient ~ '^\+[1-9][0-9]{1,14}$'),
    body text not null check (length(btrim(body)) > 0),
    status text not null default 'queued'
        check (status in ('queued', 'accepted', 'sent', 'delivered', 'failed', 'unknown')),
    provider text not null check (length(btrim(provider)) > 0),
    provider_message_id text check (
        provider_message_id is null or length(btrim(provider_message_id)) > 0
    ),
    idempotency_key text not null check (length(btrim(idempotency_key)) > 0),
    payload_hash text not null check (payload_hash ~ '^[0-9a-f]{64}$'),
    error_code text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    sent_at timestamptz,
    delivered_at timestamptz,
    unique (case_id, channel, idempotency_key),
    check (updated_at >= created_at),
    check (delivered_at is null or (sent_at is not null and delivered_at >= sent_at)),
    check (status <> 'sent' or sent_at is not null),
    check (status <> 'delivered' or delivered_at is not null)
);

create unique index case_messages_provider_message_uidx
    on public.case_messages(provider, mode, provider_message_id)
    where provider_message_id is not null;
create index case_messages_case_created_idx
    on public.case_messages(case_id, created_at desc, id desc);

-- Same private API-only access model as the other case tables.
alter table public.case_messages enable row level security;
revoke all on public.case_messages from public;
