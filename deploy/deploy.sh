#!/usr/bin/env bash
#
# Build and deploy the service to Cloud Run. Repeatable: run it again to ship a
# new revision. The service is private (no public access) and runs as the
# runtime service account.
#
# The first deploy prompts to create an Artifact Registry repo. Say yes.

source "$(dirname "$0")/_load.sh"

# `gcloud run deploy` sends a whole service spec, so anything not named on this
# command line is dropped from the service -- including the master's custom
# audience, which is what lets the Apps Script identity token authenticate.
# Losing it does not fail the deploy: it fails every later Send, as a 403 from
# Cloud Run's front end before the container is reached, which the master then
# reports as a JSON parse error on the HTML error page. So re-apply it here on
# every deploy rather than leaving it to be restored by hand.
audience_args=()
if [ -n "${MASTER_AUDIENCE:-}" ]; then
  audience_args+=(--add-custom-audiences="${MASTER_AUDIENCE}")
else
  echo "WARNING: MASTER_AUDIENCE is not set in vars.sh, so this deploy will" >&2
  echo "         drop the master's custom audience and the master will get" >&2
  echo "         403s until it is re-added. See SETUP.txt step 8." >&2
fi

# Env vars use a custom delimiter (^@@^) because ALLOWED_EMAILS / ADMIN_EMAILS
# can contain commas, which is gcloud's default delimiter.
gcloud run deploy "${SERVICE_NAME}" \
  --source "${REPO_ROOT}/service" \
  --region "${REGION}" \
  --service-account "${SA_EMAIL}" \
  --memory 1Gi \
  --set-env-vars "^@@^BIGQUERY_PROJECT=${PROJECT_ID}@@BIGQUERY_DATASET=${BQ_DATASET}@@BIGQUERY_TABLE=${BQ_TABLE}@@ALLOWED_EMAILS=${ALLOWED_EMAILS}@@ADMIN_EMAILS=${ADMIN_EMAILS}@@ALLOWED_DOMAIN=${ALLOWED_DOMAIN}@@RATE_LIMIT_PER_MIN=${RATE_LIMIT_PER_MIN}" \
  --no-allow-unauthenticated \
  "${audience_args[@]+"${audience_args[@]}"}"

echo
echo "Deployed. Service URL:"
gcloud run services describe "${SERVICE_NAME}" \
  --region "${REGION}" --format "value(status.url)"
