# Voice intake prompts

The Vapi assistant's first message and system prompt are versioned together.
The YAML file for the published version is the source of truth for future edits
to this assistant. The current first message is exactly “Bonjour, comment puis-je vous aider ?”.

| Version | Change |
| --- | --- |
| v8 | Prompt verified against the exported, published Vapi v8 assistant. |
| v9 | Published September 26, 2026. Recording announcement stays in the first message only. |
| v10 | Published September 26, 2026. Agent speech reaches the browser and server; the agent follows the server's mandatory question sequence and does not restart the introduction. |
| v11 | Capture every stated fact, including spontaneous details and corrections; normalize relative times and addresses while retaining v10's question protocol and event subscriptions. |
| v12 | Send opening conversation updates to the server so the greeting is retained in the dossier. |
| v13 | Speak only French, omit tool waiting phrases, and announce the WhatsApp follow-up at closing. |
| v14 | Announce the manager's SMS with a private deposit link after a completed call; avoid promising delivery after urgent or interrupted calls. |
| v17 | Ask only for the narrative, location and approximate incident time; keep volunteered safety details for urgent handoff. |
| v18 | Ask for narrative, location and full name; close with one short SMS sentence. |
| v19 | Only greet, listen, play the exact SMS closing and hang up after two seconds of silent audio. No follow-up questions. |
| v20 | Restore only the selected P0 (narrative, location, full name) through `next_intake_step`; close only after completion and allow longer caller pauses. |

The published YAML version, Vapi prompt, and `VOICE_ASSISTANT_VERSION` must
stay in sync. The assistant ID remains stable.
The v9 update used the Vapi assistant API because the dashboard's draft
validator rejects the existing Gradium custom transcriber. An unrelated
dashboard draft may still appear; Production CD verifies the published version.

Future prompt changes deploy automatically from `main` through Production CD.
Add a new YAML version with `status: published` and mark the previous one
`published_historical`. The workflow requires the repository Actions secret
`VAPI_PRIVATE_API_KEY`, checks that Vapi's current prompt matches a versioned
predecessor, updates only the first message and complete model object, and
verifies the prompt and client/server message subscriptions. It also passes `VOICE_ASSISTANT_VERSION` as an API
deployment runtime variable. An unversioned edit in Vapi stops the release
for review.

v20 reattaches `next_intake_step` and keeps the exact blocking `endCall` SMS
closing, permitted by the prompt only after the tool returns `complete` with no
missing P0. The API's `voice-questions-v3.yaml` remains the source of the three
questions; optional information never adds questions. A volunteered emergency
still takes priority. Errors and urgent handoff do not use the SMS closing tool.

The YAML also versions `start_speaking_plan` and `stop_speaking_plan`, deployed
and read back with the prompt. French transcript endpointing waits 1 second on
punctuation, 2 seconds without punctuation and 1.5 seconds on numbers, with a
0.8-second speech wait; caller speech can stop the assistant after 0.2 seconds.
These conservative initial settings follow the
[Vapi voice pipeline contract](https://docs.vapi.ai/customization/voice-pipeline-configuration).
They require a real call to validate perceived timing; offline tests cannot prove
absence of audible overlap. The Gradium bridge retains the two seconds of silent
PCM after the final SMS message.
