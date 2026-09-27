# Hackathon case data model

This ERD is the physical model implemented by the Alembic revision and SQL resource under `apps/api/claim_api/migrations`. Alembic is the schema migration source of truth for Supabase Postgres. It implements the [case and adapter contracts](contracts.md) while keeping the hackathon schema small. Table names in the diagram are uppercase for readability; SQL names are lowercase. `AUTH_USERS` and `STORAGE_OBJECTS` are managed by Supabase.

```mermaid
erDiagram
    AUTH_USERS ||--o{ CASES : creates
    AUTH_USERS ||--o{ APPROVALS : signs
    AUTH_USERS ||--o{ EVIDENCE_UPLOAD_INTENTS : requests
    AUTH_USERS |o--o{ AUDIT_EVENTS : acts_in

    CASES ||--o{ CASE_MESSAGES : communicates
    CASES ||--o{ EVIDENCE : contains
    CASES ||--o{ EVIDENCE_UPLOAD_INTENTS : authorizes
    CASES ||--o{ PROVIDER_RESULTS : records
    CASES ||--o{ ANALYSIS_RUNS : analyzes
    CASES ||--o{ DRAFTS : versions
    CASES ||--o{ APPROVALS : receives
    CASES ||--o{ ACTIONS : performs
    CASES ||--o{ AUDIT_EVENTS : records
    CASES ||--o{ REPORT_LINE_EDITS : corrects
    CASES ||--o{ CASE_ESTIMATES : estimates
    CASES ||--o{ CASE_QUOTES : quotes
    EVIDENCE ||--o{ CASE_QUOTES : quoted_as
    CASE_ESTIMATES |o--o{ CASE_QUOTES : attached_at_version

    STORAGE_OBJECTS ||--o| EVIDENCE : referenced_by_path
    ANALYSIS_RUNS |o--o{ DRAFTS : originates
    DRAFTS ||--o{ APPROVALS : approved_as
    DRAFTS ||--o{ ACTIONS : used_by
    APPROVALS ||--o{ ACTIONS : authorizes

    AUTH_USERS {
        uuid id PK
    }

    STORAGE_OBJECTS {
        uuid id PK
        text bucket_id
        text name
    }

    CASES {
        uuid id PK
        uuid created_by_user_id FK
        text scenario_id
        boolean synthetic
        text status
        bigint state_version
        bigint content_revision
        jsonb intake_json
        uuid current_draft_id FK
        timestamptz created_at
        timestamptz updated_at
    }

    CASE_MESSAGES {
        uuid id PK
        uuid case_id FK
        text channel
        text mode
        text recipient
        text body
        text status
        text provider
        text provider_message_id
        text idempotency_key
        text payload_hash
        text error_code
        timestamptz created_at
        timestamptz updated_at
        timestamptz sent_at
        timestamptz delivered_at
    }

    EVIDENCE {
        uuid id PK
        uuid case_id FK
        text storage_path UK
        text kind
        text source_kind
        text mode
        text mime_type
        bigint byte_size
        text client_sha256
        text checksum_status
        text role
        int display_order
        text original_filename
        timestamptz received_at
    }

    EVIDENCE_UPLOAD_INTENTS {
        uuid id PK
        uuid case_id FK
        uuid actor_user_id FK
        text storage_path UK
        text kind
        text mime_type
        bigint byte_size
        text client_sha256
        text filename
        timestamptz created_at
        timestamptz expires_at
        uuid finalized_evidence_id FK
        timestamptz finalized_at
    }

    PROVIDER_RESULTS {
        uuid id PK
        uuid case_id FK
        text provider
        text mode
        text status
        text query_hash
        text source_version
        jsonb payload_json
        text reason
        timestamptz retrieved_at
    }

    ANALYSIS_RUNS {
        uuid id PK
        uuid case_id FK
        bigint input_content_revision
        text method_version
        text status
        jsonb input_json
        jsonb output_json
        text error_code
        timestamptz started_at
        timestamptz finished_at
    }

    DRAFTS {
        uuid id PK
        uuid case_id FK
        uuid analysis_run_id FK
        uuid parent_draft_id FK
        int version
        bigint content_revision
        jsonb recipient_json
        bigint amount_minor
        text currency
        text body
        jsonb attachment_ids
        text sha256
        timestamptz created_at
    }

    APPROVALS {
        uuid id PK
        uuid case_id FK
        uuid draft_id FK
        uuid actor_user_id FK
        text draft_sha256
        bigint approved_content_revision
        timestamptz approved_at
        timestamptz superseded_at
    }

    ACTIONS {
        uuid id PK
        uuid case_id FK
        uuid approval_id FK
        uuid draft_id FK
        uuid registration_action_id FK
        text kind
        text mode
        text status
        text idempotency_key
        text payload_hash
        text reference
        timestamptz created_at
    }

    AUDIT_EVENTS {
        uuid id PK
        uuid case_id FK
        uuid actor_user_id FK
        text event_type
        bigint state_version_before
        bigint state_version_after
        bigint content_revision_before
        bigint content_revision_after
        jsonb metadata_json
        timestamptz occurred_at
    }

    REPORT_LINE_EDITS {
        uuid id PK
        uuid case_id FK
        text line_id
        text text
        text uncertainty
        jsonb source_refs_json
        text previous_text
        bigint as_of_revision
        uuid actor_user_id FK
        timestamptz created_at
    }

    CASE_ESTIMATES {
        uuid id PK
        uuid case_id FK
        int version
        jsonb line_items_json
        bigint total_minor
        text currency
        text tax_basis
        text estimate_source
        jsonb source_refs_json
        uuid created_by_user_id FK
        timestamptz created_at
    }

    CASE_QUOTES {
        uuid id PK
        uuid case_id FK
        int version
        uuid evidence_id FK
        bigint total_ttc_minor
        text amount_source
        int attached_estimate_version FK
        uuid created_by_user_id FK
        timestamptz created_at
    }
```

## Ownership and relationship details

| Relationship | Enforcement |
| --- | --- |
| Case → evidence, provider results, analysis runs, drafts, approvals, actions, audit events | SQL foreign key on `case_id`. |
| Handler → case/approval/audit event | UUID references Supabase `auth.users.id`; `audit_events.actor_user_id` may be null for a system event. |
| Storage object → evidence | **Logical link only** through `(bucket_id='claim-evidence', storage_path=name)`. Supabase owns `storage.objects`; the API checks object existence when finalizing evidence. |
| Analysis run → draft | Nullable `analysis_run_id`; a handler edit also sets `parent_draft_id` to the draft it revised. |
| Draft → approval → action | SQL foreign keys plus application checks that all records belong to the same case and that the approval matches the current content revision and draft digest. |
| Registration action → send action | Nullable `actions.registration_action_id` on a send receipt; the API requires a confirmed registration before a simulated send. |
| Case → current draft | Nullable `cases.current_draft_id`, updated transactionally with draft creation. It must refer to a draft of the same case. |

`intake_json`, provider payloads, analysis input/output, and draft attachment IDs are JSONB because their demo shapes will evolve quickly. They are validated by Pydantic before writes. Source references inside analysis output point to evidence or provider-result UUIDs, and attachment IDs point to evidence UUIDs; the API checks their existence and case ownership because JSONB does not provide row-level foreign keys for array elements. A later pilot can normalize these into proposition, source-reference, and draft-attachment tables if query needs justify it.

Logfire traces and evaluation experiments are held in Logfire, not in these case tables. Synthetic fixture definitions and golden eval cases are versioned in the repo. `analysis_runs.method_version` and `provider_results.source_version` make each stored output reproducible.

## Migration constraints and indexes

- Primary keys are UUIDs; `created_at`, `received_at`, `retrieved_at`, and `occurred_at` default to UTC server time. Required status/mode fields have SQL `CHECK` constraints matching the contract enums.
- `cases.state_version` and `cases.content_revision` are nonnegative. Mutations use `UPDATE ... WHERE id = ? AND state_version = ?` inside a transaction, then append one audit event.
- `evidence.storage_path` is unique; the private bucket path is prefixed by case ID. `client_sha256` remains explicitly unverified until `checksum_status='verified'`.
- `evidence_upload_intents` binds a two-hour, non-upsert Storage token to its handler, case, generated path, and declared metadata. It has RLS enabled and no browser policies; finalization records its evidence ID and timestamp once.
- `provider_results` has a unique constraint on `(case_id, provider, mode, query_hash, source_version)`. For fixtures, `source_version` is the fixture version; for live adapters it is an explicit provider/config version. This prevents an identical lookup from incrementing the case revision again.
- `drafts` has a unique constraint on `(case_id, version)`. `approvals` has at most one unsuperseded row per case through a partial unique index on `case_id WHERE superseded_at IS NULL`.
- `actions` has a unique constraint on `(case_id, kind, idempotency_key)` and stores a `payload_hash`. Repeating the same key and payload returns the existing receipt; a different payload with that key is a conflict.
- Index `(case_id, started_at DESC)` on analysis runs, `(case_id, version DESC)` on drafts, and `(case_id, occurred_at)` on audit events for the handler timeline.
- Enable Row Level Security on all application tables, with no `anon` or `authenticated` browser policies. The API alone uses its server-side database credential after verifying the handler JWT. The Storage bucket remains private.

The Alembic revision applies tables and constraints in dependency order, then adds the nullable `cases.current_draft_id` foreign key after `drafts` exists. No database cascade should erase audit events, approvals, or action receipts when editing case content.

## Follow-up SMS storage

Migration `20260925_case_messages` adds `case_messages` after the evidence upload intents revision, which follows the core revision. A message belongs to a case, without any draft or approval dependency. `actions` remains exclusively for approved claim registration/send.

- `channel=sms`; `mode=mock|live` distinguishes preview/simulation from provider delivery. Adding storage does not enable real SMS sending.
- `recipient` uses E.164 form; `body` is the exact message snapshot, not a dynamically regenerated case summary. The sender must treat recipient/body/payload hash as immutable after creation.
- Statuses: `queued` (local intent), `accepted` (provider accepted request, not proof of delivery), `sent`, `delivered`, `failed`, `unknown` (uncertain submission/delivery outcome). `sent_at` is required for `sent`; delivery requires ordered `sent_at` and `delivered_at`. Callback processing must tolerate out-of-order events and not regress delivered status.
- A unique `(case_id, channel, idempotency_key)` identifies one logical send. The application must compare `payload_hash` over canonical channel/mode/provider/recipient/body: same key and payload returns the existing message, different payload returns a conflict. A uniqueness constraint alone does not guarantee exactly-once delivery to an external provider. Reconcile an unknown outcome before any retry.
- Provider IDs are unique per provider and mode when present. `provider` is a stable adapter/account namespace. Store provider error codes in `error_code`, not raw responses containing secrets.
- The API must update `updated_at` and append an audit event transactionally on changes; SQL does not add an automatic timestamp trigger. Message-only writes increment case `state_version`, not `content_revision`, so delivery updates do not invalidate claim approvals. Actual intake/evidence changes still do.
- Keep message text and upload links out of audit/log metadata; record message UUID and status instead. Case-scoped upload links must retain their expiration/access checks even though the historical body is persisted.
- RLS is enabled with no browser policies; the API is the access path. Case deletion is restricted while messages exist. `(case_id, created_at DESC, id DESC)` supports the timeline.

This migration adds storage only. Message creation/send APIs, the SMS provider adapter, authenticated delivery callbacks, and retry/reconciliation logic remain to be implemented.
