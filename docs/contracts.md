# Contracts for the hackathon demo

This document specifies the boundaries in [architecture.md](architecture.md). The Python Pydantic models are the executable source of truth; FastAPI publishes their OpenAPI schema, and the TypeScript client is generated from that schema. Provider payloads never cross directly into the web app.

## Cross-cutting rules

- All HTTP routes are under `/v1`. Requests and responses are JSON except direct browser-to-Storage media transfers.
- Every case ID, evidence ID, run ID, draft ID, approval ID, and action ID is a UUID. Timestamps are UTC ISO 8601. Money is integer minor units plus ISO currency, never a float.
- Every mutation of an existing case carries `expected_state_version`. The API returns `409 stale_case` when it does not match. Responses return the new `state_version` and `content_revision`.
- `state_version` increments on every write. `content_revision` increments only when case content changes: intake, evidence, provider result, analysis selected for review, or draft. Approval and action receipts change state but not content.
- Every externally derived result has `mode: "mock" | "live"`, `provider`, `retrieved_at`, a source identifier, and an explicit result status. No `null` result is interpreted as a positive match.
- Every material claim points to one or more typed source references or is explicitly marked as a hypothesis. A plate candidate is never treated as the driver's identity.
- The backend computes draft digests and gate results. The browser displays them but cannot assert them.
- All routes require a verified Supabase Auth access JWT. The actor ID comes from its `sub` claim, never from the request body.

## Canonical domain shapes

| Shape | Required fields | Meaning |
| --- | --- | --- |
| `Case` | `id`, `created_by_user_id`, `synthetic=true`, `status`, `state_version`, `content_revision`, `scenario_id`, timestamps | The only lifecycle aggregate. |
| `Intake` | `reported_at`, `insured_reference`, `policy_reference`, `incident_at`, `location`, `vehicle_country`, `narrative`, `danger_status`, `injury_status`, `missing_fields` | Caller statements; unknown is a valid value for danger/injury status, not an invented identity or policy. |
| `Evidence` | `id`, `case_id`, `kind`, `storage_path`, `byte_size`, `client_sha256`, `checksum_status`, `mime_type`, `source_kind`, `received_at`, `mode` | Immutable file metadata. `checksum_status` is `client_declared` or `verified`; no signed URL is stored. |
| `SourceRef` | `type`, `id`, `locator` | Points to intake field, evidence region/frame/timecode, lookup row, or curated source excerpt. |
| `Proposition` | `text`, `kind`, `assessment`, `source_refs`, `uncertainty_note` | One reviewable statement from analysis. |
| `ProviderResult<T>` | `status`, `mode`, `provider`, `source_id`, `retrieved_at`, `data`, `reason` | Normalized result of a mock or later live adapter. |
| `AnalysisRun` | `id`, `input_content_revision`, `method_version`, `status`, `output`, timestamps | One bounded Pipelex attempt against an immutable case snapshot. |
| `Draft` | `id`, `version`, `recipient`, `amount`, `body`, `attachment_ids`, `content_revision`, `sha256`; optional `analysis_run_id`, `parent_draft_id` | Immutable claim draft version. The `sha256` covers normalized recipient, amount, body, and attachments. |
| `Approval` | `id`, `draft_id`, `draft_sha256`, `approved_content_revision`, `actor_id`, `approved_at` | Approval of exactly one current draft and case revision. |
| `ActionReceipt` | `id`, `kind`, `mode=mock`, `status`, `approval_id`, `draft_id`, `idempotency_key`, `reference`, `created_at`; optional `registration_action_id` | Simulated registration or send, tied to the approved draft and stored once per key. |

Enumerations:

- `Case.status`: `collecting`, `review_ready`, `approved`, `registered`, `sent`.
- `ProviderResult.status`: `matched`, `no_match`, `ambiguous`, `unavailable`, `error`.
- `AnalysisRun.status`: `running`, `ready`, `stale`, `failed`.
- `Proposition.kind`: `caller_report`, `visual_observation`, `lookup_fact`, `inference`.
- `Proposition.assessment`: `supported`, `hypothesis`, `contradicted`.
- `GateResult.status`: `passed`, `needs_review`, `blocked`.
- `ActionReceipt.status`: `confirmed`, `unknown`, `failed`.

An example source reference is `{ "type": "evidence", "id": "<uuid>", "locator": "frame:00:00:08.200" }`. The API resolves that reference to the file and exact frame or region in the handler UI. A `lookup_fact` uses the stored provider-result ID as its source.

## Browser ↔ FastAPI contract

| Method and route | Request essentials | Response / effect |
| --- | --- | --- |
| `POST /v1/cases` | `scenario_id` | `201 CaseView`; seed synthetic intake and audit event. |
| `GET /v1/cases/{case_id}` | None | `200 CaseView` with evidence, latest analysis, draft, gates, and actions. |
| `PATCH /v1/cases/{case_id}/intake` | Intake patch, `expected_state_version` | New case versions; prior approval invalidated. |
| `POST /v1/cases/{case_id}/evidence/upload-intents` | Filename, MIME type, byte size, checksum, kind, `expected_state_version` | Case-scoped Storage path, bucket, and signed upload token; no evidence row yet. The intent is actor-bound, expires after two hours, and cannot overwrite an existing object. |
| `POST /v1/cases/{case_id}/evidence` | Exact upload path, kind, checksum, `expected_state_version` | Verify object existence, stored size/MIME, and metadata against the intent; create immutable evidence metadata and bump content revision. |
| `GET /v1/cases/{case_id}/evidence/{evidence_id}/read-url` | None | Short-lived signed URL for this authorized handler. |
| `POST /v1/cases/{case_id}/demo-events/cctv-received` | Fixture event ID, `expected_state_version` | Append simulated CCTV and observations; available only in demo mode. |
| `POST /v1/cases/{case_id}/analysis-runs` | `expected_state_version` | Synchronous bounded run; `200 AnalysisRun` and updated gates/draft, or typed failure. |
| `POST /v1/cases/{case_id}/counterparty-lookup` | Full plate, country, accident date, vehicle track, case-local observation refs, identification status, optional handler reason, `expected_state_version` | Source-check the plate and involvement, persist idempotent mock insurance and UK/FR correspondent results, select current result IDs, return `CaseView`. Human confirmation records actor and time. |
| `PATCH /v1/cases/{case_id}/drafts/current` | Proposed editable fields, `expected_state_version` | Create a new immutable draft version and invalidate prior approval. |
| `POST /v1/cases/{case_id}/approvals` | `draft_id`, `expected_state_version` | `201 Approval` only if gates pass and the draft/revision are current. |
| `POST /v1/cases/{case_id}/registration` | `expected_state_version`, `Idempotency-Key` header | `201/200 ActionReceipt` with fictional `SIM-CLAIM-*` reference. |
| `POST /v1/cases/{case_id}/send` | `expected_state_version`, `Idempotency-Key` header | `201/200 ActionReceipt` with fictional `SIM-MSG-*` reference. |

`CaseView` is a read model assembled by FastAPI. It contains the latest case, intake, evidence metadata, provider results, analysis summary, current draft, gate results, approval status, and action receipts. The API supplies temporary signed read URLs through the dedicated route; they are not persisted or included in Logfire attributes. A browser-provided checksum is stored as `client_declared`: finalization checks it still matches the issued intent and checks Storage's object size and MIME metadata, but does not stream media bytes through FastAPI to rehash them. Bundled synthetic fixtures can be `verified` from known bytes.

An upload intent stores the authenticated actor, case, generated case-prefixed path, allowed MIME type, declared size/checksum, expiry, and optional finalized evidence ID. Finalization is single-use and only accepts that exact path and metadata. The API never writes to Supabase's `storage` tables. `cctv-lille-1732-v1` is a server-owned deterministic pixel-art still and fixed observation fixture; it is available only in the `complete` scenario and does not contact a camera or analyze handler uploads.

HTTP errors use one envelope:

```json
{
  "error": {
    "code": "stale_case",
    "message": "The case changed; refresh before editing.",
    "details": {"current_state_version": 7},
    "request_id": "<uuid>"
  }
}
```

`401/403` means missing/invalid session or forbidden actor; `404` unknown case/object; `409` stale version, stale analysis, or duplicate conflicting idempotency key; `422` invalid transition or blocked gate with structured gate results; `502` normalized provider failure; `503` analysis unavailable. The browser never guesses whether an unknown send succeeded: it shows `unknown` for manual reconciliation.

## Application ↔ provider contracts

Adapters are Python protocols with typed inputs and `ProviderResult<T>` outputs. The application handles errors, retries, persistence, and gate decisions. A fixture implementation and a later live implementation must return the same type.

| Port | Input | Successful output | Demo implementation |
| --- | --- | --- | --- |
| `IntakeSource.read` | Scenario ID or transcript | Structured caller statements + provenance | Versioned transcript fixture. |
| `EvidenceAnalyzer.analyze` | Evidence IDs and signed read locations | Observations tied to exact image/frame | Versioned observation fixture. |
| `CameraLocator.search` | Location and time interval | Camera candidates and request metadata | Camera list fixture. |
| `CCTVReceiver.receive` | Case and fixture event ID | New evidence and source metadata | Explicit demo event; never a real camera request. |
| `InsuranceLookup.lookup` | Plate candidate and incident date | Coverage match, no match, or ambiguity | Fictional policy rows. |
| `CorrespondentLookup.lookup` | Insurer candidate and date | Correspondent candidate with source | Fictional directory row. |
| `ClaimsAnalyzer.analyze` | `AnalysisInputV2` snapshot | `AnalysisOutputV1` | Live Pipelex method. |
| `ClaimRegistry.register` | Current approval and draft | Claim reference receipt | Local simulated action. |
| `ClaimSender.send` | Current approval, reference, and attachment IDs | Message receipt | Local simulated outbox; no network send. |

The Pipelex method sees a bounded snapshot, not a database handle or Storage secret. `AnalysisInputV2` contains case ID, content revision, structured intake, normalized observations, provider results, current lookup, estimate/quote, bounded voice facts, and cited source excerpts. `AnalysisOutputV1` contains proposed route, recipient candidate, amount candidate, propositions, contradictions, critical missing items, and separate non-blocking notes for optional evidence gaps. An amount candidate must cite the current estimate or remain absent. A draft requires at least one supported, source-cited proposition. Input and output carry `schema_version=2` and `schema_version=1` respectively. The API rejects unknown source IDs, malformed money, missing required fields, and an output that silently promotes a hypothesis to a fact.

Provider failures are data, not empty successes. A mock lookup that cannot establish coverage returns `status="ambiguous"` or `"no_match"`, with a reason; it never returns a fabricated insurer. Every provider result is persisted with its fixture version so a Logfire trace can be matched to a reproducible scenario.

Before starting a Pipelex run, the application resolves and persists any changed provider results. The idempotency identity is `(case_id, provider, mode, query_hash, source_version)`, where `query_hash` represents the normalized query and `source_version` is the fixture or live-provider configuration version. Repeated identical results leave `content_revision` unchanged. Each result's source ID is its persisted row ID. The Pipelex input snapshot is read only after that step, so its source IDs and input revision refer to persisted records.

The S14 lookup uses `mock-insurance-v2`. Its insurance query includes normalized full plate, country, accident date, selected vehicle track, identification status, and cited case-local observation refs. Human confirmation also stores its reason, actor, and timestamp. The UK correspondent query uses `{insurer_id, accident_country: "FR", incident_date, insurance_result_id}`; gate 2 requires those fields to match the selected insurance result. `CaseView.counterparty_lookup` identifies the current insurance and correspondent result UUIDs and reports `current=false` after an accident-date correction. The previous provider results remain in the audit trail and cannot supply a current recipient. Legacy `lookup-fixtures-v1` results remain readable under their original query contract.

## Deterministic gate contract

| Gate | Pass condition | When it fails |
| --- | --- | --- |
| 1. Intake | Identity/account reference, location, and incident date/time are present; danger/injury status is recorded, including `unknown` if the caller cannot answer. | Ask targeted questions; do not invent fields. A reported immediate danger calls for human escalation in the UI. |
| 2. Evidence/lookup | A usable plate candidate, dated coverage result, and recipient candidate have mutually consistent source references. | Mark missing, ambiguous, or contradictory; skip automatic lookup/recipient selection and require review. |
| 3. Analysis | Every material proposition cites an existing source or is labelled as hypothesis; contradictions and missing items are visible. | Draft cannot be approved until edited or rerun. |
| 4. Approval | Current draft ID/digest and current `content_revision` exactly match an approval by the authenticated handler. | Registration and send return `422 approval_required`. |

The gate engine runs in application code, not in the model prompt. It returns `GateResult[]` with `gate`, `status`, `reason_codes`, and `source_refs`. Only the application changes lifecycle status.

### State and concurrency rules

1. The case starts in `collecting`. Once gates 1–3 pass and a current draft exists, it moves to `review_ready`.
2. Approving that draft moves it to `approved`. Registration moves it to `registered`; simulated send moves it to `sent`.
3. A content change creates a new `content_revision`, supersedes approval, and returns to `collecting` or `review_ready` according to the gates. Action receipts remain in the audit trail.
4. Analysis stores the input revision before inference. If the revision has changed when it returns, persist the run as `stale` and do not replace the current draft.
5. Registration and send run in a database transaction with a unique key on `(case_id, kind, idempotency_key)`. Repeating the same request returns the stored receipt. A different payload with the same key is a conflict. An `unknown` send is never retried automatically.

## Minimal Postgres contract

Use migrations for `cases`, `evidence`, `provider_results`, `analysis_runs`, `drafts`, `approvals`, `actions`, and append-only `audit_events` as specified in the [ERD](erd.md). The `cases` row has `state_version`, `content_revision`, current draft ID, and current status. Structured but evolving demo payloads live in validated JSONB columns. UUIDs, foreign keys, `NOT NULL` on critical fields, and unique idempotency constraints stay in SQL. Enable Row Level Security on case tables without `anon` or `authenticated` browser policies; the API uses a server-only database credential after JWT verification. Storage is private. The handler identity from Supabase Auth is recorded on approvals and actions.

The use case service updates a case with an optimistic version check inside a transaction, then appends an audit event with actor, event type, versions before/after, and affected record IDs. No audit event contains raw image bytes or a secret token.

## Evaluation dataset contract

Each eval case has `dataset_case_id`, `fixture_version`, `AnalysisInputV1`, `expected_gate_statuses`, `expected_missing_items`, and any expected proposition/source relationships. The runner invokes the same `ClaimsAnalyzer` port used by the API, so evals exercise the deployed analysis contract. Evaluators produce assertions for valid source IDs, preserved uncertainty, required fields, and draft structure; reviewed quality scores can assess wording. Eval output is advisory and cannot change a live case or approval.

## S03/S04 implemented case material

`CaseView` now includes a deterministic `report`, a current versioned `estimate`, a current PDF `quote`, and `quote_status` (`no_estimate`, `no_quote`, `matched`, `mismatch`, `outdated`). Each report line contains `text`, `claim_kind`, `source_refs`, `uncertainty`, `as_of_revision`, and `stale`. Handler corrections also carry signer, signing time, and previous text; the append-only audit event records both old and new text. Evidence-presence lines describe receipt and metadata, not visual damage. Analysis lines become stale after material changes and must not be presented as current.

S15 analysis uses `AnalysisInputV2` (`schema_version=2`) and method `claims-analysis-v2.0.0`. The persisted input snapshot carries only case-local intake, current visual excerpts with media locators/timecodes, the selected S14 lookup, current estimate/quote, and bounded voice facts; it omits call audio and transcripts. Source refs additionally support `estimate` (`total_minor`, `currency`, `estimate_source`, `version`, or `line_items.<id>`) and `voice` (`facts.<index>`). The output remains `AnalysisOutputV1` because its proposed route, classified propositions, contradictions, missing items, recipient, amount, and non-blocking notes still cover the result. The raw V2 input, raw V1 output, method version, status, and gates are stored in `analysis_runs`. Gate 2 now requires a current S14 association with a case-local plate and movement observation; a plate in the intake narrative alone cannot pass. For G1, Gate 3 requires a supported proposition citing a current visual observation. A current estimate requires an exact, cited amount; a mismatched quote blocks the gate. No unverified legal citation is supplied to Pipelex.

| Route | Request | Effect |
| --- | --- | --- |
| `PATCH /v1/cases/{id}/report-lines/{line_id}` | Corrected text, optional uncertainty, expected state version | Sign and audit a correction; increment content revision; revoke approval. |
| `PUT /v1/cases/{id}/estimate` | Unique `{id,label,amount_minor}` items, exact total in cents, EUR/TTC, expected state version | Create an immutable estimate version; reject negative, floating, nonmatching totals. |
| `PUT /v1/cases/{id}/quote` | Finalized case-owned PDF evidence ID, manually entered TTC cents, expected state version | Associate or replace a PDF quote, remembering its estimate version. |
| `DELETE /v1/cases/{id}/quote` | Expected state version | Append a removal version; revoke approval. |

The G1 1 240 € estimate is stored as a `demo_fixture` hypothesis made of 480, 180, 260, 240, and 80 € posts. The PDF filename comes from the signed upload intent; old uploads with no stored name display an explicit unavailable label. A quote amount remains `handler_entered` until verified extraction exists. Approval checks the current draft amount against the estimate, blocks differing quote totals and outdated quote associations, and requires that the current quote PDF be attached to the draft. Other PDFs cannot be attached as quote substitutes. The migration adds `report_line_edits`, `case_estimates`, `case_quotes`, and private evidence filename metadata.

## Follow-up message storage

`case_messages` stores an initial SMS independently of `ActionReceipt` and claim approval. See the [ERD message contract](erd.md#follow-up-sms-storage) for fields, status semantics, idempotency, and version rules. S09 adds manager-only preview, creation and history endpoints in simulation mode. The [SMS private-link pilot](sms-private-link.md) provides live invitation delivery. The [garage reply flow](garage-sms.md) adds a transactional outbox after photo finalization, with signed, idempotent, monotone delivery callbacks and simulation enabled by default.

## S10 minimal deposit-grant contract for S09

`public.deposit_grants` holds an opaque-link SHA-256 digest, case ID, capabilities, content revision, expiry, revocation, grant version, optional associated message ID, and creation time. It never holds the raw token. The composite foreign key `(case_id, message_id)` prevents association with another case's SMS. The table is private with RLS enabled and no browser policy. Grant creation, association and revocation append token-free audit events where appropriate; none changes case content or approval.

The server-side Python interface is `DepositGrantService(database_url, portal_base_url)` from `claim_api.deposit_grants`:

| Operation | Contract |
| --- | --- |
| `issue(case_id, content_revision, ttl=24h, *, actor_id=None, expected_state_version=None)` | Return `IssuedDepositGrant(grant_id, url, expires_at)` once. The URL is `<DEPOSIT_PORTAL_BASE_URL>#token=<opaque bearer>`; the token stays in the browser fragment. A manager-facing caller passes `actor_id`; a trusted internal S09 caller has already checked ownership. Each call creates a distinct grant; there is no silent extension. TTL is positive and at most seven days. |
| `validate_url(cursor, grant_id, case_id, url, content_revision)` | During S09 message creation, lock the grant and case rows, verify the exact URL and token digest, expiry, revocation, capability, case and current revision, and require no previous association. Use the same database transaction and cursor as the message insert. |
| `associate(cursor, grant_id, message_id, case_id)` | Call after inserting the message, before commit. It sets the one-time message association only for the same case and only while the grant is still current. Roll back the entire transaction on failure. |
| `validate(token, case_id, required_capability='read_summary')` | For a future insured portal, check an **already associated** grant's case, expiry, revocation and capability on each operation. A correction by the insured may advance the case revision without killing the active link; preview/association alone require the originally issued revision. A guest route must separately enforce its session and scope; no guest route is activated by this slice. |
| `revoke(grant_id, case_id, *, actor_id=None, expected_state_version=None)` | Revoke immediately. Manager-facing callers pass `actor_id`; revocation never changes message body or delivery history. |

Manager-only routes: `POST /v1/cases/{case_id}/deposit-grants` takes `{expected_state_version, content_revision, ttl_seconds?}` and returns `{grant_id, url, expires_at}` with `Cache-Control: no-store` and `Referrer-Policy: no-referrer`; `POST /v1/cases/{case_id}/deposit-grants/{grant_id}/revoke` takes `{expected_state_version}` and returns 204. Both require the normal authenticated manager bearer and case ownership. Set `DEPOSIT_PORTAL_BASE_URL` to the full HTTPS page URL, such as `https://example.test/depot` (localhost HTTP is allowed for development). The separate guest routes are described below.

S09 stores its exact sent URL only in the private immutable `case_messages.body`, never in `deposit_grants`, logs, audit, or list exports. Its creation route accepts an authenticated preview's URL only to revalidate it server-side with `validate_url`; it must rebuild the body from current case facts and associate the grant in the insertion transaction. The raw token must not appear in a Referer to another origin, localStorage, or errors. Errors use `invalid_grant`, `expired_grant`, `revoked_grant`, `forbidden_capability`, `stale_case` or `grant_already_associated` without case content or token.

## S09 simulated follow-up SMS

Set `DEPOSIT_PORTAL_BASE_URL` to the insured deposit page and, for caller-number lookup, `VAPI_PRIVATE_API_KEY` and `VOICE_ASSISTANT_ID`. The manager confirms the caller-number candidate, or enters an E.164 correction with a reason. Caller ID is a contact candidate only. The server checks Vapi's call and assistant IDs. No Twilio request is made by these routes.

S17 adds `POST /v1/voice/web-test/session` for the authenticated demo handler and an allowed web origin. It returns a non-cacheable, five-minute Vapi public JWT restricted to the current origin and `VOICE_ASSISTANT_ID`, signed server-side with `VAPI_PRIVATE_API_KEY`; the private key never reaches the browser. `GET /v1/voice/web-test/calls/{call_id}` resolves only that owner's `web` voice session to its case. Browser calls store `telephony_provider=web` and create `voice_web` synthetic cases. They have no caller number and cannot use the S09 `caller_id` recipient path. The browser displays final transcript turns; the original Vapi stereo recording continues through the existing private Storage path.

| Route | Behaviour |
| --- | --- |
| `GET /v1/cases/{case_id}/messages/recipient-candidate` | Returns a masked caller number if the latest inbound Vapi call has one. |
| `POST /v1/cases/{case_id}/messages/preview` | Takes `{expected_state_version, recipient_confirmation}`. Creates a short-lived, unassociated deposit grant and returns the exact proposed body, `deposit_url`, grant ID, preview hash, content revision, expiry, missing items, source refs and warnings. It does not create a message. |
| `POST /v1/cases/{case_id}/messages` | Takes `{expected_state_version, idempotency_key, recipient_confirmation, deposit_grant_id, deposit_url, preview_hash}`. Rebuilds the body from current facts, validates the grant and inserts the immutable `mock` message plus grant association in one transaction. The deposit URL is accepted only after S10 verifies its token, case, revision and expiry. Same key and payload returns HTTP 200; a new message returns HTTP 201. |
| `GET /v1/cases/{case_id}/messages` | Returns history with masked recipient and token-free body preview. |
| `GET /v1/cases/{case_id}/messages/{message_id}/link` | Authenticated manager fallback for an active, associated deposit link. Response uses `Cache-Control: no-store` and `Referrer-Policy: no-referrer`. |
| `POST /v1/cases/{case_id}/messages/{message_id}/mock-transition` | Takes `{expected_state_version, status, error_code?}`. Simulates `queued → accepted → sent → delivered`, `failed` or `unknown` and reconciliation from `unknown`. It advances state version, not content revision; it never changes the stored body or recipient. |

The exact URL lives only in the private immutable `case_messages.body`; `deposit_grants` stores its token digest. Preview and fallback responses are authenticated and marked `Cache-Control: no-store`. In the UI, an expired or stale preview must be regenerated. A history list never reveals a reusable token. For a local demonstration, run the API with a disposable PostgreSQL database migrated to head, set `DEPOSIT_PORTAL_BASE_URL=https://example.test/depot`, create a G1 case, and use the **SMS de demande de pièces** panel to preview and create a simulated message. The simulation status is explicit; it is not proof of delivery.

## S10 insured deposit portal

The web route `/depot` reads the bearer from the SMS URL fragment, removes the fragment from browser history, and sends the bearer only in an `Authorization` header to `GET /v1/deposit/session`. The exchange succeeds only for a grant already associated with a `case_messages` row. It creates a random opaque session token stored only as SHA-256 in private `deposit_sessions`; the response is `Cache-Control: no-store` and the session expires after 15 minutes or with the grant, whichever comes first. The browser keeps the session in memory. On reload or expiry, the insured reopens the SMS link. A revoked or expired grant invalidates every session immediately at the next API call.

All routes below require `Authorization: Bearer <guest-session-token>` and derive `case_id` from the associated grant. They do not accept a case UUID from the guest. The manager bearer cannot be used here, and the guest bearer cannot authorize manager routes. The app sets `Referrer-Policy: no-referrer` and does not place either bearer in localStorage or query strings.

| Route | Capability | Request and result |
| --- | --- | --- |
| `GET /v1/deposit/summary` | `read_summary` | Redacted intake facts, P1 gaps, time provenance, transcription and recording availability, guest-submitted evidence metadata. No provider lookup, internal notes, media path or correspondence details. |
| `POST /v1/deposit/corrections` | `correct_intake` | `{expected_state_version, field, value, reason?}`. Limited intake fields only. Creates `insured_corrections` with previous/new value and session/grant, increments case state/content revisions, invalidates current approval and draft; `409 stale_case` on conflict. Original voice transcript and recording remain intact. |
| `POST /v1/deposit/evidence/upload-intents` | `upload_evidence` | Same request and signed upload policy as S02, with `deposit_grant_id` and `source_kind=insured_upload` recorded on the intent. |
| `POST /v1/deposit/evidence` | `upload_evidence` | Same finalization contract as S02, restricted to the active grant's intent. Reads the private object to verify size, SHA-256 and file signature against MIME before insertion. An interrupted/mismatched upload never becomes evidence. Provenance is `insured_upload`, checksum status `verified`. |
| `GET /v1/deposit/evidence/{id}/read-url` | `read_summary` | A short private read URL only for insured or demo evidence on this case. |
| `GET /v1/deposit/demo-media` and `GET /v1/deposit/demo-media/{key}/preview` | `attach_demo_media` | Scenario-scoped G1/G2/G3 synthetic catalogue with authenticated previews, subject, provenance, thumbnail key and video duration. Only whitelisted files from the case's scenario are returned; no reference annotations or generation prompts are exposed. |
| `POST /v1/deposit/demo-media/{key}/attach` | `attach_demo_media` | `{expected_state_version}`; idempotently attaches one fixture to this case, increments content revision and invalidates approval, without copying Vision output. |

Error codes distinguish `invalid_grant`, `invalid_session`, `expired_grant`, `expired_session`, `revoked_grant`, `forbidden_capability`, `stale_case`, `invalid_upload` and `storage_unavailable`. The portal uses `DEPOSIT_PORTAL_BASE_URL=https://<web-host>/depot` for links created by S09. The session exchange itself needs `DATABASE_URL`; evidence routes also need the existing private Supabase Storage variables. Signed upload URLs and read URLs remain short lived.
