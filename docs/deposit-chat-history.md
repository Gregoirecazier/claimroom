# Private chat: name collection and reopening

The private deposit flow asks for `insured_name` (first and last name) when it is
missing, then the accident timestamp, then photos. The insured confirms a proposed
name before `/corrections` saves it. The manager's missing-information list also
includes the name for older cases whose stored list predates this requirement.

Apply Alembic revision `20260927_deposit_history` before deploying the API and web
app. `GET /v1/deposit/chat-history` and `PUT /v1/deposit/chat-history` store the
opening recap, both speakers' messages, current phase, and pending proposal in
`deposit_chat_history`, keyed by case. Access requires a valid case-scoped guest
session; expired and revoked links retain their normal restrictions. No raw link,
session token, unsent draft or unsubmitted file is stored in the history payload.
The table enables RLS and grants no access to public, anon or authenticated roles.

Writes use a separate optimistic revision and serialize on the case. Repeating an
identical save is idempotent; overwriting existing messages or a newer checkpoint
returns `409 stale_chat_history`. The browser serializes saves, retries an
ambiguous failed write before newer turns, and shows a save error instead of
silently reporting success. A failed history load does not create a blank thread.
History never writes claim facts or sends messages to the insured.

Opening the SMS link in a new tab restores server history. The same-tab draft and
unfinished file warning remain local. Legacy same-case tab history is imported
only if no server history exists. Earlier assistant messages lost before this
feature cannot be reconstructed from the database.

Validation covers name confirmation/reopening, pending proposals, new sessions,
case isolation, revocation, stale writes, idempotency, network retries, API payload
validation and loading failures. PostgreSQL tests use a disposable local database.
