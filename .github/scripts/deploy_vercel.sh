#!/usr/bin/env bash
set -euo pipefail

# A project-scoped token can deploy, but cannot inspect deployments via the
# Vercel CLI. The deploy command itself waits for READY. Bound that wait so a
# BLOCKED build cannot hold the production release queue indefinitely.
: "${VERCEL_TOKEN:?VERCEL_TOKEN is required}"
: "${VERCEL_ORG_ID:?VERCEL_ORG_ID is required}"
: "${VERCEL_PROJECT_ID:?VERCEL_PROJECT_ID is required}"
: "${VERCEL_PUBLIC_URL:?VERCEL_PUBLIC_URL is required}"
: "${VERCEL_READINESS_KIND:?VERCEL_READINESS_KIND is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"

deploy_timeout="${VERCEL_DEPLOY_MAX_WAIT_SECONDS:-600}"
public_timeout="${VERCEL_PUBLIC_MAX_WAIT_SECONDS:-120}"
poll_seconds="${VERCEL_PUBLIC_POLL_SECONDS:-10}"
if ! [[ "$deploy_timeout" =~ ^[1-9][0-9]*$ && "$public_timeout" =~ ^[1-9][0-9]*$ \
  && "$poll_seconds" =~ ^[0-9]+$ ]]; then
  echo "Invalid Vercel deployment wait settings." >&2
  exit 1
fi
if ! [[ "$VERCEL_PUBLIC_URL" =~ ^https://[a-zA-Z0-9.-]+/?$ ]]; then
  echo "VERCEL_PUBLIC_URL must be an HTTPS origin." >&2
  exit 1
fi
if [[ "$VERCEL_READINESS_KIND" != "api" && "$VERCEL_READINESS_KIND" != "web" ]]; then
  echo "VERCEL_READINESS_KIND must be api or web." >&2
  exit 1
fi

deploy_args=(--prod --yes --token "$VERCEL_TOKEN")
if [[ "$VERCEL_READINESS_KIND" == "api" ]]; then
  : "${VOICE_ASSISTANT_VERSION:?VOICE_ASSISTANT_VERSION is required for the API deployment}"
  deploy_args+=(--env "VOICE_ASSISTANT_VERSION=$VOICE_ASSISTANT_VERSION")
fi

echo "Waiting up to ${deploy_timeout}s for Vercel to build and promote $VERCEL_READINESS_KIND."
stdout_file="$(mktemp)"
body_file="$(mktemp)"
trap 'rm -f "$stdout_file" "$body_file"' EXIT
set +e
deploy_args=(--prod --yes --token "$VERCEL_TOKEN")
if [[ "$VERCEL_READINESS_KIND" == "api" ]]; then
  : "${VOICE_ASSISTANT_VERSION:?VOICE_ASSISTANT_VERSION is required for the API deployment}"
  deploy_args+=(--env "VOICE_ASSISTANT_VERSION=$VOICE_ASSISTANT_VERSION")
fi
timeout --signal=TERM --kill-after=5s "${deploy_timeout}s" \
  vercel deploy "${deploy_args[@]}" | tee "$stdout_file"
pipeline_status=("${PIPESTATUS[@]}")
set -e
deploy_status="${pipeline_status[0]}"
tee_status="${pipeline_status[1]}"
if (( deploy_status != 0 )); then
  echo "Vercel deploy failed or timed out (exit $deploy_status, limit ${deploy_timeout}s). Check the deployment URL printed above." >&2
  exit "$deploy_status"
fi
if (( tee_status != 0 )); then
  echo "Could not capture the Vercel deployment URL (tee exit $tee_status)." >&2
  exit "$tee_status"
fi
deployment_url="$(tail -n 1 "$stdout_file" | tr -d '\r')"
if ! [[ "$deployment_url" =~ ^https://[a-zA-Z0-9.-]+\.vercel\.app/?$ ]]; then
  echo "Vercel did not return a valid completed deployment URL." >&2
  exit 1
fi
echo "url=$deployment_url" >> "$GITHUB_OUTPUT"
echo "Vercel completed deployment: $deployment_url"

# The immutable URL may be SSO-protected even after READY, while the public
# production alias is reachable. Check that alias only after deploy succeeds.
deadline=$(( $(date +%s) + public_timeout ))
last_state="not_checked"
while (( $(date +%s) < deadline )); do
  path="/"
  if [[ "$VERCEL_READINESS_KIND" == "api" ]]; then
    path="/health/live"
  fi
  if http_code="$(timeout 20s curl --silent --show-error --max-time 15 \
    --output "$body_file" --write-out '%{http_code}' \
    "${VERCEL_PUBLIC_URL%/}${path}")"; then
    last_state="HTTP $http_code"
    if [[ "$http_code" == "200" ]]; then
      if [[ "$VERCEL_READINESS_KIND" == "api" ]] && jq -e '.status == "ok"' "$body_file" > /dev/null 2>&1; then
        echo "Public API alias is healthy: ${VERCEL_PUBLIC_URL%/}/health/live"
        exit 0
      elif [[ "$VERCEL_READINESS_KIND" == "web" ]] && grep -q '<title>Claimroom' "$body_file"; then
        echo "Public web alias serves Claimroom: ${VERCEL_PUBLIC_URL%/}/"
        exit 0
      fi
      last_state="HTTP 200 with unexpected $VERCEL_READINESS_KIND content"
    fi
  else
    last_state="transport error"
  fi
  echo "Public $VERCEL_READINESS_KIND alias not ready ($last_state); retrying."
  sleep "$poll_seconds"
done

echo "Vercel deployment $deployment_url completed, but public $VERCEL_READINESS_KIND alias did not pass within ${public_timeout}s (last state: $last_state): $VERCEL_PUBLIC_URL" >&2
exit 1
