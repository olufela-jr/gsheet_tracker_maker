#!/usr/bin/env bash
#
# Build and deploy the service to Cloud Run. Repeatable: run it again to ship a
# new revision. The service is private (no public access) and runs as the
# runtime service account.
#
# The first deploy prompts to create an Artifact Registry repo. Say yes.

source "$(dirname "$0")/_load.sh"

gcloud run deploy "${SERVICE_NAME}" \
  --source "${REPO_ROOT}/service" \
  --region "${REGION}" \
  --service-account "${SA_EMAIL}" \
  --set-env-vars "BIGQUERY_PROJECT=${PROJECT_ID},BIGQUERY_DATASET=${BQ_DATASET},BIGQUERY_TABLE=${BQ_TABLE},MASTER_SHEET_ID=${MASTER_SHEET_ID},TEMPLATE_SHEET_ID=${TEMPLATE_SHEET_ID}" \
  --no-allow-unauthenticated

echo
echo "Deployed. Service URL:"
gcloud run services describe "${SERVICE_NAME}" \
  --region "${REGION}" --format "value(status.url)"
