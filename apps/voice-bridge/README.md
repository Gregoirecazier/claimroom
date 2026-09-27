# Claimroom voice bridge

Always-on Vapi custom transcriber and voice endpoints backed by Gradium. This
service mounts the existing `claim_api.gradium_bridge` router without the
Vercel API's cold-start dependency installation.

Build from the repository root with `apps/voice-bridge/Dockerfile`. Required
service variables: `GRADIUM_BRIDGE_ENABLED=true`, `GRADIUM_API_KEY`,
`GRADIUM_VOICE_ID`, `VAPI_AUDIO_SECRET`, and `VAPI_TRANSCRIBER_URL_TOKEN`.
`railway.json` selects the Dockerfile and `/health/live` health check.

The Vapi assistant points its custom transcriber at
`wss://<bridge-domain>/v1/voice/gradium/transcriber?token=<scoped-token>`.
Vapi currently omits configured authentication headers on custom-transcriber
WebSocket handshakes; the separate random URL token authenticates only this
endpoint. Keep this URL out of application logs and do not reuse the token for
other endpoints. The TTS endpoint remains
`https://<bridge-domain>/v1/voice/gradium/tts` with `X-Vapi-Secret`.
The business webhooks and tool calls remain on the Claimroom API.

A 200 health response confirms configuration and serving; the production
acceptance check is a real inbound call with a complete transcript and the
original recording visible in the application.
