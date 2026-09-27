# Hackathon implementation plan

This plan turns the [architecture](architecture.md), [contracts](contracts.md), and [ERD](erd.md) into a working demo. The repository currently contains documentation only. The goal is a deployed, end-to-end synthetic claim that a handler can review, approve, register, and send **in simulation**. Pipelex analysis and Logfire telemetry are live; every other external provider is a labelled fixture.

## The demo we are building

1. Sign in as the invited handler and create a synthetic case from a scenario.
2. Review caller statements, evidence, and mock lookup results. Add a synthetic photo or trigger the simulated CCTV arrival.
3. Run live Pipelex analysis. Show sourced propositions, uncertainty, missing items, and deterministic gate results.
4. Edit a versioned draft, approve that exact version, then simulate registration and sending. Show fictional receipts and an audit timeline.
5. Show a second, ambiguous case where the gates stop approval until the missing or conflicting information is resolved.

The app must never initiate a real call, camera request, insurer lookup, claim registration, or message. Dust, voice, and live vision are outside this hackathon build. The [target workflow](sequence-diagram.md) remains the longer-term reference.

## Build order

Each milestone leaves a runnable vertical slice. Finish its exit check before expanding scope.

| Milestone | Implement | Exit check |
| --- | --- | --- |
| **0. Prove the risky path** | Create minimal `apps/api` FastAPI app; deploy it as a Vercel Python Function; import Pipelex; execute one tiny typed method against the configured text model; emit a Logfire span. | A deployed endpoint completes one synthetic analysis within a measured request budget; its trace appears in the EU Logfire project. Record bundle size, cold start, and elapsed time. If local Pipelex execution does not fit, switch only the `ClaimsAnalyzer` adapter to Pipelex's hosted execution before building the rest. |
| **1. Establish the foundation** | Scaffold `apps/web`, `apps/api`, Alembic migrations under `apps/api/claim_api/migrations`, and `evals`; pin dependencies and lockfiles; add environment examples and local startup. Provision Supabase Auth, Postgres, and a private `claim-evidence` bucket. Add API JWT verification, CORS for the web origin, and database connection handling. | Local web and API run; deployed web can sign in and call an authenticated `/v1/me` endpoint; unauthenticated calls fail. No secret appears in the browser bundle or repo. |
| **2. Complete the fixture-backed case path** | Migrate the ERD; implement case creation, intake updates, read model, audit trail, fixture adapter interfaces, and a seeded scenario. Add optimistic versions and `mode=mock` provenance. Build the handler's case list/detail, intake, source, timeline, and gate views. Start with a deterministic `ClaimsAnalyzer` fixture so the full workflow can be exercised before prompt work. | A handler creates and edits a case; refresh preserves the state; stale edits return `409`; all provider results are visibly simulated. |
| **3. Add evidence and mock enrichment** | Seed small synthetic media in private Storage. Implement signed upload intent, direct browser upload, object verification/finalization, and signed read URL. Add mock vision observations, insurer/correspondent results, and the explicit simulated CCTV event. | A photo and CCTV fixture appear with provenance and source locators; the browser needs an API-issued signed URL to read a private object, and media bytes never pass through FastAPI. Ambiguous plate and unavailable lookup remain ambiguous in the UI. |
| **4. Connect live analysis and gates** | Implement `AnalysisInputV1`/`AnalysisOutputV1`, the versioned Pipelex method, `ClaimsAnalyzer` adapter, source-reference validation, and gates 1–3. Persist runs and input revisions; reject stale results; expose failures without fabricating an answer. Replace fixture analysis in the demo path with Pipelex while keeping the fixture implementation for deterministic tests. Add API/Pipelex spans to Logfire. | The complete seeded case produces a reviewable, sourced draft; the ambiguous case cannot be approved. A case edit during analysis cannot replace the current draft with an old result. A model/provider failure leaves the case safely editable. |
| **5. Close the human workflow** | Add immutable draft versions and digest, edit and approval endpoints, gate 4, and simulated registration/send adapters. Store idempotency keys, receipts, and audit events. Build review, approval, and simulated action controls. | Approval is tied to the current draft and content revision. Any case-content or draft edit invalidates it. Registration precedes send. Double-clicks or retries return the same receipt; no network send exists. |
| **6. Evaluate and release** | Add five golden synthetic cases and offline Pydantic Evals; send experiment traces to Logfire. Run focused API/domain tests, build checks, and a production smoke test. Deploy both Vercel projects with explicit environment variables; document seed/reset and demo steps. | The happy path and blocked path work from public deployment URLs; the eval run is inspectable; seeded data can be reset; the demo can be repeated without manual database repair. |

Milestone 0 is a feasibility gate, not a separate product. If it fails, keep the same API/domain contract and change the analysis adapter. Do not spend time polishing the UI until the live analysis path is viable.

## Concrete work packages

### Repository and delivery

- `apps/api`: Python 3.12 FastAPI app, Pydantic contracts, domain services, SQL adapters, provider ports, Pipelex method/config, small fixtures. Use a tracked `uv.lock`.
- `apps/web`: React, Vite, strict TypeScript, generated client from FastAPI OpenAPI, Supabase Auth session, case and review screens. Use a tracked npm lockfile.
- `apps/api/claim_api/migrations`: Alembic revision and SQL resource for schema, checks, indexes, and RLS. Alembic is the sole schema migration source; keep schema changes in revisions, not startup code. Supabase Storage bucket policy/configuration remains separate infrastructure setup.
- `evals`: versioned synthetic inputs, expected outcomes, deterministic evaluators, and a command to run experiments.
- CI: install/build/type-check web; install/import/check API; run focused tests and validate the migration syntax against a disposable database when available. Keep deployment configuration and required environment variable names documented.

### API and database

Implement the routes in [contracts.md](contracts.md) and the tables in [erd.md](erd.md). Keep HTTP handlers thin: authenticate, parse, call a use-case service, serialize. The service owns transactions, version checks, transitions, and audit events. Provider adapters return normalized `ProviderResult<T>` with provenance; they do not alter case status themselves.

The case read model should make the UI simple: one request returns current intake, evidence metadata, provider results, latest run, current draft, gate results, approval state, actions, and timeline. Include a `request_id` in errors and traces so a failed demo action can be found in Logfire.

### Frontend

Build one focused handler flow, not a general claims platform:

1. **Sign-in and case picker:** one invited user, scenario creation, case resume.
2. **Case workspace:** intake fields, evidence/source viewer, mock-result labels, missing-item prompts, audit timeline.
3. **Analysis panel:** run/retry, in-progress and failure states, propositions grouped by source and uncertainty, gate results.
4. **Review panel:** editable recipient/amount/body/attachments, current draft version and digest, approve, simulated register/send, receipts.

Buttons for blocked transitions should explain the exact gate or missing field. The UI must distinguish a caller report, an observed fact, a lookup result, and a model inference.

### Pipelex, Logfire, and evals

- Keep Pipelex to one bounded analysis method. It receives a typed, persisted case snapshot and returns a typed proposal; it cannot approve or send.
- Validate every returned source ID and any proposed amount/recipient before creating a draft. Deterministic gates remain Python code.
- Instrument request, fixture lookup, database, Pipelex, validation, and gate spans. Include IDs, revisions, mode, outcome, latency, and usage; omit secrets, signed URLs, and binary evidence.
- Evaluate the complete case, ambiguous plate, missing CCTV, contradictory date, and unsupported liability claim. Require schema validity, valid citations, preserved uncertainty, and no invented positive coverage. Review prose quality separately.

## Decisions to make while implementing

| Decision | Default | Decide by |
| --- | --- | --- |
| Text model for Pipelex | Use one configured model already accessible to the team; pin its model ID and method version. | Milestone 0 smoke test. |
| Pipelex execution mode | Direct execution inside FastAPI. | Milestone 0 latency/bundle measurement. |
| Synthetic seed media | One small photo and one CCTV-derived fixture with known provenance. | Milestone 3. |
| Demo account | One invite-only Supabase Auth user. | Milestone 1 deployment. |
| Draft amount | Absent until a synthetic cost estimate supports it; a handler may enter an amount during review. | Milestone 4 contract implementation. |

These choices do not change the public API. Keep credentials in local/Vercel secret stores; the Logfire project already exists, but its write token still needs to be created when instrumentation is wired.

## Definition of done for the hackathon

- Two deployed Vercel projects and one Supabase project support the entire synthetic demo without a developer console.
- Live Pipelex analysis returns a typed, source-checked result, and the run is visible in Logfire.
- All other providers and final actions are visibly mocked; registration and send create only local receipts.
- The complete case reaches `sent`; the ambiguous case stays blocked; the handler can explain why from the UI.
- Draft edits revoke prior approval, stale analysis cannot overwrite the current draft, and duplicate action requests cannot create duplicate receipts.
- The five-case eval run and a short demo/reset guide are reproducible from the repository.

## Scope after the demo

Real voice intake, vision, CCTV requests, insurer/BCF lookups, Dust, insurer-system registration, and actual message delivery require separate provider validation, permissions, and operational design. They are not dependencies for this implementation plan.
