# Claimroom voice agent configuration

`assistant.json` is the desired published Vapi assistant configuration. The
current prompt is versioned in `config/prompts/voice-intake/v20.yaml` and must
match this snapshot, including the tool bindings and speaking plans. The assistant
collects only the selected P0: narrative, location, first and last name. It calls
`next_intake_step` after each completed answer, asks only its next missing question,
and uses the blocking `endCall` SMS closing only after `complete`. The bridge
adds two seconds of silent audio after that closing. The final report persists
the dossier.
`tool.json` defines the reattached `next_intake_step` tool. Its empty
`request-start` message disables Vapi's default filler during triage.

The production CD workflow applies these files before deploying the API. It
uses the existing `VAPI_PRIVATE_API_KEY` GitHub Actions secret, checks the
published assistant and tool for unrelated drift, updates the changed prompt,
tool, and closing fields, then reads both back to verify them. API changes take
effect directly; the dashboard's separate unsaved draft is not used.

The JSON records every non-secret assistant and tool setting from the active
versions. Vapi owns the actual custom-voice secret and transcriber URL token;
the placeholders in `assistant.json` mean they must already be present in
Vapi. Keep these secret values out of Git. A change to another setting should
be made in these manifests and supported by the sync script, rather than
edited in the dashboard.
