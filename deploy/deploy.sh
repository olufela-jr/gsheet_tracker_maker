#!/usr/bin/env bash
#
# Build and deploy the service to Cloud Run. Repeatable: run it again to ship a
# new revision. The service is private (no public access) and runs as the
# runtime service account.
#
# The first deploy prompts to create an Artifact Registry repo. Say yes.

source "$(dirname "$0")/_load.sh"

# Env vars use a custom delimiter (^@@^) because ALLOWED_EMAILS / ADMIN_EMAILS
# can contain commas, which is gcloud's default delimiter.
gcloud run deploy "${SERVICE_NAME}" \
  --source "${REPO_ROOT}/service" \
  --region "${REGION}" \
  --service-account "${SA_EMAIL}" \
  --memory 1Gi \
  --set-env-vars "^@@^BIGQUERY_PROJECT=${PROJECT_ID}@@BIGQUERY_DATASET=${BQ_DATASET}@@BIGQUERY_TABLE=${BQ_TABLE}@@ALLOWED_EMAILS=${ALLOWED_EMAILS}@@ADMIN_EMAILS=${ADMIN_EMAILS}@@ALLOWED_DOMAIN=${ALLOWED_DOMAIN}@@RATE_LIMIT_PER_MIN=${RATE_LIMIT_PER_MIN}" \
  --no-allow-unauthenticated

echo
echo "Deployed. Service URL:"
gcloud run services describe "${SERVICE_NAME}" \
  --region "${REGION}" --format "value(status.url)"
