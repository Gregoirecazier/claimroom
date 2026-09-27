"""Persist verified media identity and reusable, scope-bound video observations."""

from alembic import op


revision = "20260925_video_reuse"
down_revision = "20260925_report_estimate_quote"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table public.evidence add column sha256_verified text check (sha256_verified ~ '^[0-9a-f]{64}$')")
    op.execute("alter table public.evidence drop constraint evidence_checksum_status_check")
    op.execute("alter table public.evidence add constraint evidence_checksum_status_check check (checksum_status in ('client_declared', 'verified', 'mismatch'))")
    op.execute("alter table public.evidence add constraint evidence_verified_hash_check check ((checksum_status = 'verified') = (sha256_verified is not null)) not valid")
    # Older fixtures may claim verification without storing any digest. They
    # must be reverified from Storage before being eligible for reuse.
    op.execute("update public.evidence set checksum_status = 'client_declared' where checksum_status = 'verified' and client_sha256 is null")
    op.execute("update public.evidence set sha256_verified = client_sha256 where checksum_status = 'verified'")
    op.execute("alter table public.evidence validate constraint evidence_verified_hash_check")
    # 20260925_report_estimate_quote already provides the composite unique key.
    op.execute("""
        create table public.media_analysis_artifacts (
            id uuid primary key default gen_random_uuid(),
            scope text not null check (length(scope) > 0),
            sha256_verified text not null check (sha256_verified ~ '^[0-9a-f]{64}$'),
            pipeline_fingerprint text not null check (pipeline_fingerprint ~ '^[0-9a-f]{64}$'),
            pipeline_spec jsonb not null check (jsonb_typeof(pipeline_spec) = 'object'),
            status text not null check (status in ('running', 'succeeded', 'failed')),
            attempt_id uuid not null,
            lease_until timestamptz,
            observations_json jsonb check (observations_json is null or jsonb_typeof(observations_json) = 'array'),
            output_schema_version integer not null,
            usage_json jsonb not null default '{}'::jsonb check (jsonb_typeof(usage_json) = 'object'),
            failure_count integer not null default 0 check (failure_count >= 0),
            failure_history jsonb not null default '[]'::jsonb check (jsonb_typeof(failure_history) = 'array'),
            last_error_code text,
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now(),
            finished_at timestamptz,
            unique (scope, sha256_verified, pipeline_fingerprint),
            check ((status = 'succeeded') = (observations_json is not null)),
            check ((status = 'running') = (lease_until is not null))
        )
    """)
    op.execute("""
        create table public.case_media_analysis_links (
            case_id uuid not null,
            evidence_id uuid not null,
            artifact_id uuid not null references public.media_analysis_artifacts(id) on delete restrict,
            linked_at timestamptz not null default now(),
            primary key (case_id, evidence_id, artifact_id),
            foreign key (case_id, evidence_id) references public.evidence(case_id, id) on delete restrict
        )
    """)


def downgrade() -> None:
    op.execute("drop table public.case_media_analysis_links")
    op.execute("drop table public.media_analysis_artifacts")
    op.execute("alter table public.evidence drop constraint evidence_verified_hash_check")
    op.execute("alter table public.evidence drop constraint evidence_checksum_status_check")
    op.execute("alter table public.evidence add constraint evidence_checksum_status_check check (checksum_status in ('client_declared', 'verified'))")
    op.execute("alter table public.evidence drop column sha256_verified")
