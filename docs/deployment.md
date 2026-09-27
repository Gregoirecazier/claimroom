# Vercel and Supabase deployment

Use two Vercel projects, an always-on Railway voice bridge, and one
non-production Supabase project for the demo. This keeps the browser bundle,
API credentials, and Vapi's WebSocket audio bridge separate. GitHub Actions
runs production releases in this order: CI gates, selected Vapi synchronization,
Supabase Alembic migrations, Railway voice bridge deployment, API deployment, web
deployment, then production smoke checks. Components with no relevant changes
since the last verified release are skipped.

## Vercel projects

Create both projects from the same Git repository and leave the framework build
settings on automatic detection:

| Project | Root directory | Build |
| --- | --- | --- |
| Web | `apps/web` | Vercel detects Vite, runs `npm run build`, and serves `dist`; `vercel.json` sends client-side routes to `index.html`. |
| API | `apps/api` | Vercel detects FastAPI at `app.py`; `pyproject.toml` limits the runtime to Python 3.12. Dependencies are in `pyproject.toml` and `requirements.txt`. |

Set the web project's `VITE_API_BASE_URL` to the deployed API origin, then add
that exact web origin to the API project's `CORS_ORIGINS`. Configure these
values for both Preview and Production if both deployment targets are used.

## Supabase setup and migrations

Use a dedicated demo project and an invited handler account. Create a private
`claim-evidence` Storage bucket with a 50 MiB file limit and allowed MIME types
`image/jpeg`, `image/png`, `image/webp`, `application/pdf`, `video/mp4`,
   `video/quicktime`, and `video/webm`. The API retains a 5 MiB limit for photos/PDFs. Verify direct
signed uploads from the deployed web origin.

The G1 MP4 is about 5.03 MiB and is seeded by the API when a G1 case is
created. Browser uploads are capped at 5 MiB for images/PDFs and 50 MiB for videos.
Include `claim_api/fixture_media/g1/` in the API deployment; the media must not
be served from `apps/web/public`.

When the migration files or API dependencies change, the checked-in Alembic
revisions are applied automatically after CI succeeds and before either Vercel
project deploys. The GitHub Actions workflow uses
`MIGRATION_DATABASE_URL`, a Supabase direct or session-capable TLS URL. The
application's `DATABASE_URL` should use Supabase's transaction pooler with
`sslmode=require`; keep the migration URL separate and server-side. Use
expand/contract migrations so the currently live app remains compatible if a
Vercel deployment fails after the schema step.

## Environment variables

Set variables in each Vercel project's Settings → Environment Variables. Keep
Preview and Production values scoped to the matching Supabase project.

| Project | Variable | Value and handling |
| --- | --- | --- |
| Web | `VITE_API_BASE_URL` | Public HTTPS origin of the API deployment. |
| Web | `VITE_SUPABASE_URL` | Supabase project URL; this is used in browser code. |
| Web | `VITE_SUPABASE_PUBLISHABLE_KEY` | Supabase publishable/anon key; browser-visible by design. |
| API | `APP_ENV` | Set to `production`. |
| API | `CORS_ORIGINS` | Comma-separated exact web origins, with no trailing slash. |
| API | `SUPABASE_URL` | Supabase project URL. |
| API | `SUPABASE_JWKS_URL` or `SUPABASE_JWT_SECRET` | Configure the JWKS URL for asymmetric signing, or the JWT secret for legacy HS256 signing. Keep secrets server-side. |
| API | `DATABASE_URL` | Server-only Supabase transaction-pooler PostgreSQL URL with `sslmode=require`. |
| API | `SUPABASE_SERVICE_ROLE_KEY` | Server-only Storage administration key. Never put this in a `VITE_` variable. |
| API | `SUPABASE_STORAGE_BUCKET` | Optional; defaults to `claim-evidence`. |
| API | `WHATSAPP_DELIVERY_MODE` | `mock` by default; set `fake_whatsapp` to deliver messages to the private browser phone. |
| API | `DEPOSIT_PORTAL_BASE_URL` | Public HTTPS `/depot` URL used in the follow-up message and fake phone. |
| API | `SMS_LINK_DELIVERY_MODE`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_SMS_MESSAGING_SERVICE_SID`, `TWILIO_SMS_WEBHOOK_BASE_URL` | Set the mode to `twilio` to send the private link to the phone caller. The final claim SMS reuses that invitation's recipient. |
| API | `CLAIM_NOTIFICATION_MODE`, `CLAIM_EMAIL_TO` | Set `live` to send the final SMS and email when the handler clicks the send button after approval. `CLAIM_EMAIL_TO` is the controlled test mailbox; the legacy `CLAIM_DEMO_INSURER_EMAIL` remains a fallback. |
| API | `RESEND_API_KEY`, `CLAIMROOM_EMAIL_FROM`, `CLAIMROOM_EMAIL_REPLY_TO` | Resend credentials and sender details for the final email. For the test sender, use `onboarding@resend.dev` as the from address. |
| API | `ANALYSIS_PROVIDER` | `gemini` (default), `pipelex` for text-only legacy analysis, or `hybrid` for Pipelex without media and Gemini with a photo, video, or PDF. |
| API | `GEMINI_API_KEY` | Server-only Google key for joint photo/video analysis. |
| API | `GEMINI_MODEL` | Defaults to `gemini-3.8-flash`. |
| API | `OPENAI_API_KEY` | Required for `pipelex` and for text-only runs in `hybrid` mode. |
| API | `LOGFIRE_TOKEN` | Optional EU Logfire write token for API and analysis traces. |

## Automated production release

Production deployments are disabled by default in this public repository. The
hosted demo continues to deploy from the private development repository. To
enable deployment from your own copy, configure your own provider resources and
all required secrets, then set the repository variable
`ENABLE_PRODUCTION_DEPLOYMENTS=true`. No production credentials are bundled.

Add these GitHub Actions **secrets** to the repository:

| Secret | Purpose |
| --- | --- |
| `AUTHORIZED_DEPLOY_AUTHOR_EMAIL` | Private email of the commit author authorized by the Vercel team. Never log this value. |
| `MIGRATION_DATABASE_URL` | Supabase direct/session TLS connection used only by Alembic. |
| `VERCEL_ORG_ID` | Vercel team/scope ID shared by both projects. |
| `VERCEL_API_PROJECT_ID` | ID of the `claimroom-demo-api` Vercel project. |
| `VERCEL_WEB_PROJECT_ID` | ID of the `claimroom-demo-web` Vercel project. |
| `VERCEL_API_TOKEN` | Long-lived token scoped only to the API project. |
| `VERCEL_WEB_TOKEN` | Long-lived token scoped only to the web project. |
| `RAILWAY_TOKEN` | Project token scoped to the production environment of the Railway project currently hosting S08. |
| `DEMO_HANDLER_PASSWORD` | Password for one confirmed synthetic Supabase Auth handler used by post-deploy smoke. |

Add these GitHub Actions **variables** (public deployment origins, without a
trailing slash):

| Variable | Example |
| --- | --- |
| `API_PRODUCTION_URL` | `https://claimroom-demo-api.vercel.app` |
| `WEB_PRODUCTION_URL` | `https://claimroom-demo-web.vercel.app` |
| `SUPABASE_URL` | Public HTTPS URL of the demo Supabase project. |
| `SUPABASE_PUBLISHABLE_KEY` | Public key used for demo handler sign-in. |
| `DEMO_HANDLER_EMAIL` | Email of the confirmed synthetic handler. |

The Vercel project IDs must point to projects configured with root directories
`apps/api` and `apps/web`. Set each project's Production runtime environment
variables in Vercel before the first release. The web project needs
`VITE_API_BASE_URL`, `VITE_SUPABASE_URL`, and
`VITE_SUPABASE_PUBLISHABLE_KEY`; the API project needs the server-side values
listed above. `VITE_API_BASE_URL` must match `API_PRODUCTION_URL`, and the API's
`CORS_ORIGINS` must include `WEB_PRODUCTION_URL`.
The CD workflow runs Vercel CLI from the repository root so each project's
configured root directory exists in the uploaded build context. It selects the
existing Vercel projects using `VERCEL_ORG_ID` and `VERCEL_PROJECT_ID` instead
of running `vercel link`; that command requests a user profile unavailable to
the project-scoped deployment tokens.
The published `VOICE_ASSISTANT_VERSION` is passed as a runtime variable on the
API deployment itself. This avoids the separate `vercel env update` command,
which cannot retrieve project settings with the project-scoped API token.

The S08 bridge currently runs in Railway project `asclepios-api`, environment
`production`, service `api`, at `https://api-production-431d.up.railway.app`.
The workflow pins those project/environment/service IDs so it cannot create a
new service or accidentally deploy elsewhere. `railway.json` builds
`apps/voice-bridge/Dockerfile` from the repository root. Create a Railway
project token scoped to that production environment and save it in GitHub as
`RAILWAY_TOKEN` before merging this workflow. The voice job uploads the
current commit with a unique marker, waits for that deployment's `SUCCESS`
status, and checks `/health/live`. The final smoke checks the bridge again.
`railway.json` limits Railway watch paths to the voice bridge source, its
Dockerfile dependency `apps/api/claim_api/gradium_bridge.py`, and the config
file itself. The first change also bumps `config/claimroom-voice-bridge.version`
to force one build as the shared service moves off its old watch paths. The
Railway production service settings have been synchronized with the three
required paths plus `/config/claimroom-voice-bridge.version` as an optional
bootstrap path. Later commits without changes to watched
files may return `SKIPPED`; the workflow accepts that only when Railway reports
exactly `No changes to watched files`, the skipped deployment used all three
Claimroom watch paths, an earlier deployment remains `SUCCESS`, and the live
voice bridge passes `/health/live`. Every other skip fails the release.
Railway config-as-code does not persist values to the service settings, so
keep these production service settings aligned with `railway.json` (or migrate
them to Railway IaC). A skipped deployment reporting stale shared-service paths
fails the guard. Check the deployment metadata before treating the release as
healthy.
Moving to a dedicated Claimroom Railway project later requires changing the
workflow IDs, Vapi transcriber/TTS URLs, and health URL together.

Automatic Git deployments are disabled for every branch in both `vercel.json`
files. Pull requests use GitHub CI without Vercel Preview deployments. This
avoids Vercel builds competing with production and prevents a Git deployment
from racing ahead of the database migration. Production CD is
the single production path when explicitly enabled: it runs on every push to `main` and can also be
started with GitHub Actions → Production CD → Run workflow. Before any other
job, it checks that the commit author matches the private
`AUTHORIZED_DEPLOY_AUTHOR_EMAIL` secret for the Vercel team owner. An absent
secret or a different author skips the entire release; the changes ship with
the next authorized commit on `main`. An authorized release requires passing
API tests, frontend build, and offline Alembic rendering before it runs the
selected migration and deployments using scoped tokens, and
signs in as the synthetic handler to check case creation, live analysis, and
the Gate 2 block when no S14 observation-backed vehicle association exists.
This creates one synthetic case per release and never sends a real registration
or message. A positive approval/send smoke requires an observation-backed S14
fixture in production; it is not currently part of this release gate. A failed CI gate
leaves the database untouched; a failed migration stops both deployments.

Vercel checks the Git commit author's access even when the CLI has an
authorized deployment token. The author gate prevents a blocked deployment
from occurring after the production migration has started.

Production migrations must be backward-compatible with the currently live
application because the schema update completes before the new app version
deploys. Migrations never run from a Vercel build or function startup.

Vercel's deploy command waits for the build and production promotion. CD wraps
it in a strict ten-minute timeout and preserves its nonzero exit status; a
stalled build therefore cannot hold the release queue indefinitely. Only after
the command succeeds does CD poll the public API alias for JSON
`{"status":"ok"}` or the public web alias for a Claimroom page, with a separate
two-minute bound. Both jobs also have a fifteen-minute GitHub Actions ceiling.
The CLI prints the immutable deployment URL during build, and the workflow
reports that URL or the last public HTTP state on failure. A project-scoped
Vercel token cannot run `vercel inspect` (`User not found`), while the immutable
deployment URL is SSO-protected even when `READY`; public alias health alone
could mistake an old deployment for the new one. One prior release remained at
`Building…` for over an hour and was later `BLOCKED` without build logs. The
cause of that Vercel state remains unknown.

`LOCAL_DEV_AUTH`, `LOCAL_DEV_BEARER_TOKEN`, and
`DATABASE_ALLOW_INSECURE_LOCAL` are for local development only. Do not define
them on Vercel. No model or Logfire credential belongs in Git, the browser
project, or CI.

## Selective production deployments

The `plan` job compares the release commit to the most recent successful
Production CD run whose **Exercise production handler flow** job succeeded,
ordered by last update so reruns of older commits are taken into account.
It ignores successful runs that skipped production (including the author gate
and documentation-only runs). This includes changes from earlier unauthorized
commits and merges replaced in the concurrency queue; comparing only with
`push.before` would lose those changes. GitHub Actions needs read-only
`actions: read` permission to find that checkpoint. Renames are compared as a
deletion plus an addition so both affected components are selected.

| Changed files | Selected deployment jobs |
| --- | --- |
| `apps/web/` (excluding Markdown and `.test.ts`/`.test.tsx`) | Web |
| `apps/api/` runtime, fixtures, or non-voice `config/prompts/` | API |
| `apps/api/claim_api/migrations/`, including SQL/data, or `apps/api/alembic.ini` | Migrations and API |
| API `pyproject.toml`, `uv.lock`, or `requirements.txt` | Migrations, Vapi and API (shared Python environment) |
| `config/prompts/voice-intake/`, `config/vapi/`, or either Vapi synchronization script | Vapi and API |
| `apps/voice-bridge/`, `railway.json`, `.dockerignore`, the bridge version marker, or Railway deployment script | Railway bridge |
| `apps/api/claim_api/gradium_bridge.py` (copied into the bridge image) | Railway bridge and API |
| Vercel deployment script | API and web |
| Markdown, `docs/`, API tests/evaluations, web `.test.ts`/`.test.tsx`, deployment test scripts, `.gitignore`, `LICENSE` | No deployment |
| Workflow/planner changes or any other unclassified path | All components |

Selections accumulate across changed files. Vapi changes also deploy the API so
its `VOICE_ASSISTANT_VERSION` stays aligned. An API-only deployment reads and
validates that version locally from the published YAML without calling Vapi.
The complete CI gates still run on every authorized release. Preflight requires
credentials only for selected deployments and the smoke checks. Skipped jobs do
not block later selected jobs; failures or cancellation do. The end-to-end
production smoke still runs after any selected deployment, checking the whole
live system, and is skipped when no deployment is needed. The Actions summary
shows the comparison commit and each component's Deploy/Skip decision.

If history is unavailable, a checkpoint is not an ancestor of the release,
or a more recent run failed, was cancelled, or is unfinished, the planner selects
a full release. This also repairs partial deployments when a later commit
reverts a change back to the last successful tree. The history scan is bounded
to 1,000 runs; reaching that limit also selects everything because an older
rerun outside the window could have changed production.

Use **Production CD → Run workflow → force_all** to rerun every deployment,
especially after changing production secrets or provider environment settings
that Git cannot detect. The existing author gate still applies. The first release
of this workflow deploys everything because the workflow itself changed.

## CI coverage and live analysis

GitHub Actions builds the web app, runs the API suite against a disposable
PostgreSQL service, and renders the Alembic chain as SQL on pull requests. It
uses no production credentials. The production release reuses those CI gates
before connecting to Supabase.

The API defaults to joint media analysis with Gemini when `GEMINI_API_KEY` is
set. See [media analysis setup](gemini-media-analysis.md) for bucket settings,
request duration and the deployment smoke test. A missing key leaves analysis requests as recorded failures, while case
and evidence routes remain available. `LOGFIRE_TOKEN` enables EU Logfire
tracing; create a write token in the EU project and keep it in the API project.
Check one complete and one ambiguous synthetic case after deployment.
The analysis method and each Vapi `next_intake_step` turn emit
`gen_ai.operation.name=invoke_agent` spans, so they appear in Logfire's Agents
view when `LOGFIRE_TOKEN` is configured. The spans also carry
`openinference.span.kind=AGENT` for the second convention that Logfire supports.
The voice span covers our server-side
decision and sourced fact extraction; Vapi-hosted model tokens and audio remain
in Vapi's own call logs. These spans contain only agent/version/provider
metadata, not transcript text, claim identifiers, or tool arguments.

## Public repository checks

The `Secret scan` workflow runs Gitleaks on every push and pull request, with
redacted output and the standard detection rules. CI also runs the application
tests on pushes to `main` and pull requests. Local recordings, audit exports,
session files, environment secrets and private key files are excluded from Git;
only placeholder `.env.example` files belong in the repository.
