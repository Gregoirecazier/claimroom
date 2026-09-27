#!/usr/bin/env bash
set -euo pipefail

: "${RAILWAY_TOKEN:?RAILWAY_TOKEN is required}"
: "${RAILWAY_PROJECT_ID:?RAILWAY_PROJECT_ID is required}"
: "${RAILWAY_ENVIRONMENT_ID:?RAILWAY_ENVIRONMENT_ID is required}"
: "${RAILWAY_SERVICE_ID:?RAILWAY_SERVICE_ID is required}"
: "${VOICE_BRIDGE_PRODUCTION_URL:?VOICE_BRIDGE_PRODUCTION_URL is required}"
: "${GITHUB_RUN_ID:?GITHUB_RUN_ID is required}"
: "${GITHUB_SHA:?GITHUB_SHA is required}"

max_wait_seconds="${RAILWAY_DEPLOY_MAX_WAIT_SECONDS:-900}"
poll_seconds="${RAILWAY_DEPLOY_POLL_SECONDS:-20}"
if ! [[ "$max_wait_seconds" =~ ^[1-9][0-9]*$ && "$poll_seconds" =~ ^[0-9]+$ ]]; then
  echo "Invalid Railway deployment wait settings." >&2
  exit 1
fi
if ! [[ "$VOICE_BRIDGE_PRODUCTION_URL" =~ ^https://[a-zA-Z0-9.-]+/?$ ]]; then
  echo "VOICE_BRIDGE_PRODUCTION_URL must be an HTTPS origin." >&2
  exit 1
fi

# This marker makes a pending deployment distinguishable from an older SUCCESS.
marker="claimroom-cd-${GITHUB_RUN_ID}-${GITHUB_SHA}"

check_voice_health() {
  for attempt in 1 2 3; do
    if curl --fail --silent --show-error --max-time 15 \
      "${VOICE_BRIDGE_PRODUCTION_URL%/}/health/live" | jq -e '.status == "ok"' > /dev/null; then
      return 0
    fi
    sleep 5
  done
  return 1
}

timeout 180s railway up --detach --yes \
  --project "$RAILWAY_PROJECT_ID" \
  --environment "$RAILWAY_ENVIRONMENT_ID" \
  --service "$RAILWAY_SERVICE_ID" \
  --message "$marker"

deadline=$(( $(date +%s) + max_wait_seconds ))
last_status="not_found"
while (( $(date +%s) < deadline )); do
  deployments="$(timeout 30s railway deployment list \
    --project "$RAILWAY_PROJECT_ID" \
    --environment "$RAILWAY_ENVIRONMENT_ID" \
    --service "$RAILWAY_SERVICE_ID" \
    --limit 20 --json)"
  deployment="$(jq -c --arg marker "$marker" '[.[] | select(.meta.cliMessage == $marker)] | first // empty' <<< "$deployments")"
  if [[ -n "$deployment" ]]; then
    deployment_id="$(jq -er '.id' <<< "$deployment")"
    status="$(jq -er '.status' <<< "$deployment")"
    if [[ "$status" != "$last_status" ]]; then
      echo "Railway deployment $deployment_id state: $status"
      last_status="$status"
    fi
    case "$status" in
      SUCCESS)
        if check_voice_health; then
          echo "Railway voice bridge ready: $deployment_id"
          exit 0
        fi
        echo "Railway deployment succeeded but voice bridge health failed: $deployment_id" >&2
        exit 1
        ;;
      SKIPPED)
        reason="$(jq -r '.meta.skippedReason // ""' <<< "$deployment")"
        if [[ "$reason" != "No changes to watched files" ]]; then
          echo "Railway deployment skipped for unexpected reason: $deployment_id" >&2
          exit 1
        fi
        if ! jq -e '
          (["/apps/voice-bridge/**", "/apps/api/claim_api/gradium_bridge.py", "/railway.json"]
            - (.meta.serviceManifest.build.watchPatterns // [])) | length == 0
        ' <<< "$deployment" > /dev/null; then
          echo "Railway skipped with stale watch patterns; Claimroom voice source may not be deployed: $deployment_id" >&2
          exit 1
        fi
        skipped_at="$(jq -er '.createdAt' <<< "$deployment")"
        live="$(jq -c --arg skipped_at "$skipped_at" \
          '[.[] | select(.status == "SUCCESS" and .createdAt < $skipped_at)]
            | sort_by(.createdAt) | last // empty' <<< "$deployments")"
        if [[ -z "$live" ]]; then
          # A busy release queue can put the last healthy deployment outside
          # the short list used for polling this run's marker.
          history="$(timeout 30s railway deployment list \
            --project "$RAILWAY_PROJECT_ID" \
            --environment "$RAILWAY_ENVIRONMENT_ID" \
            --service "$RAILWAY_SERVICE_ID" \
            --limit 1000 --json)"
          live="$(jq -c --arg skipped_at "$skipped_at" \
            '[.[] | select(.status == "SUCCESS" and .createdAt < $skipped_at)]
              | sort_by(.createdAt) | last // empty' <<< "$history")"
        fi
        if [[ -z "$live" ]]; then
          echo "Railway skipped but no earlier live SUCCESS deployment exists: $deployment_id" >&2
          exit 1
        fi
        live_id="$(jq -er '.id' <<< "$live")"
        if check_voice_health; then
          echo "Railway skipped unchanged voice files; existing live deployment is healthy: $live_id"
          exit 0
        fi
        echo "Railway skipped but the existing voice bridge failed health: $live_id" >&2
        exit 1
        ;;
      FAILED|CRASHED|REMOVED|CANCELED)
        echo "Railway deployment stopped in state $status: $deployment_id" >&2
        exit 1
        ;;
      WAITING|PENDING|BUILDING|DEPLOYING|INITIALIZING|QUEUED)
        ;;
      *)
        echo "Unrecognized Railway deployment state $status: $deployment_id" >&2
        exit 1
        ;;
    esac
  fi
  sleep "$poll_seconds"
done

echo "Railway deployment did not become healthy within ${max_wait_seconds}s (last state: $last_status)." >&2
exit 1
