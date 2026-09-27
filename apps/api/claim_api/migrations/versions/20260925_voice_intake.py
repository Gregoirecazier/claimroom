"""Persist versioned voice sessions and source-bearing final events."""

from alembic import op

revision = "20260925_voice_intake"
down_revision = "20260925_video_reuse"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        create table public.voice_sessions (
            id uuid primary key default gen_random_uuid(),
            case_id uuid not null unique references public.cases(id) on delete restrict,
            provider text not null check (provider = 'vapi'),
            provider_call_id text not null check (length(btrim(provider_call_id)) > 0),
            mode text not null check (mode in ('mock', 'live')),
            telephony_provider text not null check (telephony_provider = 'twilio'),
            speech_provider text not null check (speech_provider = 'gradium'),
            call_started_at timestamptz not null,
            assistant_id text not null,
            assistant_version text not null,
            extractor_version text not null,
            sequence bigint not null check (sequence >= 1),
            triage_json jsonb not null check (jsonb_typeof(triage_json) = 'object'),
            projected_intake_json jsonb not null check (jsonb_typeof(projected_intake_json) = 'object'),
            transcript_json jsonb not null check (jsonb_typeof(transcript_json) = 'array'),
            facts_json jsonb not null check (jsonb_typeof(facts_json) = 'array'),
            recording_status text not null default 'pending' check (recording_status in ('pending', 'available', 'unavailable', 'error')),
            recording_storage_path text,
            recording_mime_type text,
            recording_byte_size bigint,
            recording_sha256 text check (recording_sha256 is null or recording_sha256 ~ '^[0-9a-f]{64}$'),
            recording_error_code text,
            updated_at timestamptz not null default now(),
            unique (provider, provider_call_id)
        )
    """)
    op.execute("""
        create table public.voice_session_events (
            id uuid primary key default gen_random_uuid(),
            session_id uuid not null references public.voice_sessions(id) on delete restrict,
            source_event_key text not null check (length(btrim(source_event_key)) > 0),
            sequence bigint not null check (sequence >= 1),
            event_type text not null check (event_type in ('final_turn', 'end_of_call_report', 'call_failed')),
            payload_sha256 text not null check (payload_sha256 ~ '^[0-9a-f]{64}$'),
            transcript_json jsonb not null check (jsonb_typeof(transcript_json) = 'array'),
            facts_json jsonb not null check (jsonb_typeof(facts_json) = 'array'),
            triage_json jsonb not null check (jsonb_typeof(triage_json) = 'object'),
            received_at timestamptz not null,
            unique (session_id, source_event_key),
            unique (session_id, sequence)
        )
    """)
    op.execute("create index voice_session_events_session_idx on public.voice_session_events(session_id, sequence desc)")
    op.execute("alter table public.voice_sessions enable row level security")
    op.execute("alter table public.voice_session_events enable row level security")


def downgrade() -> None:
    op.execute("drop table public.voice_session_events")
    op.execute("drop table public.voice_sessions")
