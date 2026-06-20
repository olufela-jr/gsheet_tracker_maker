#!/usr/bin/env bash
#
# Project setup: create the project (if needed), link billing, enable APIs, and
# create the runtime service account. Idempotent: safe to re-run, and works
# whether PROJECT_ID is a new or an existing project.
#
# Make sure the correct gcloud account is active first:
#   gcloud config set account YOUR_EMAIL

source "$(dirname "$0")/_load.sh"

if gcloud projects describe "${PROJECT_ID}" >/dev/null 2>&1; then
  echo "Project ${PROJECT_ID} exists, skipping create."
else
  gcloud projects create "${PROJECT_ID}"
fi

gcloud config set project "${PROJECT_ID}"

if [ "$(gcloud billing projects describe "${PROJECT_ID}" \
      --format='value(billingEnabled)' 2>/dev/null)" = "True" ]; then
  echo "Billing already linked, skipping."
else
  gcloud billing projects link "${PROJECT_ID}" \
    --billing-account "${BILLING_ACCOUNT}"
fi

gcloud services enable \
  sheets.googleapis.com \
  bigquery.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com

if gcloud iam service-accounts describe "${SA_EMAIL}" >/dev/null 2>&1; then
  echo "Service account ${SA_EMAIL} exists, skipping create."
else
  gcloud iam service-accounts create "${SA_NAME}" \
    --display-name "Tracker generator runtime"
fi

# Artifact Registry repo for source deploys. Creating it here means deploy.sh
# never stops to prompt for it.
if gcloud artifacts repositories describe cloud-run-source-deploy \
    --location="${REGION}" >/dev/null 2>&1; then
  echo "Artifact Registry repo exists, skipping create."
else
  gcloud artifacts repositories create cloud-run-source-deploy \
    --repository-format=docker --location="${REGION}" \
    --description="Cloud Run source deploys"
fi

# --- BigQuery audit log: dataset, table, and least-privilege access ---------
if [ -n "${BQ_DATASET:-}" ]; then
  if bq --project_id="${PROJECT_ID}" show --dataset "${PROJECT_ID}:${BQ_DATASET}" >/dev/null 2>&1; then
    echo "Dataset ${BQ_DATASET} exists, skipping create."
  else
    bq --project_id="${PROJECT_ID}" mk --dataset \
      --location="${BQ_LOCATION}" "${PROJECT_ID}:${BQ_DATASET}"
  fi

  if bq --project_id="${PROJECT_ID}" show "${PROJECT_ID}:${BQ_DATASET}.${BQ_TABLE}" >/dev/null 2>&1; then
    echo "Table ${BQ_TABLE} exists, skipping create."
  else
    bq --project_id="${PROJECT_ID}" mk --table \
      "${PROJECT_ID}:${BQ_DATASET}.${BQ_TABLE}" \
      event_id:STRING,created_at:TIMESTAMP,spreadsheet_id:STRING,url:STRING,title:STRING,client:STRING,sub_brand:STRING,created_by:STRING,status:STRING,service_revision:STRING
  fi

  # Running queries (per-spreadsheet authorization reads created_by) needs the
  # Job User role at the project level; dataset access alone is not enough.
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/bigquery.jobUser" --condition=None >/dev/null

  # Grant the service account write access to this dataset only, via the
  # dataset ACL (works without the IAM-on-datasets allowlist). Idempotent: the
  # WRITER entry is only added if it is not already present.
  _ds_json="$(mktemp)"
  bq --project_id="${PROJECT_ID}" --format=prettyjson \
    show "${PROJECT_ID}:${BQ_DATASET}" > "${_ds_json}"
  python3 - "${_ds_json}" "${SA_EMAIL}" <<'PY'
import json, sys
path, sa = sys.argv[1], sys.argv[2]
with open(path) as f:
    ds = json.load(f)
access = ds.setdefault("access", [])
writer_roles = {"WRITER", "roles/bigquery.dataEditor"}
if not any(e.get("userByEmail") == sa and e.get("role") in writer_roles for e in access):
    access.append({"role": "WRITER", "userByEmail": sa})
with open(path, "w") as f:
    json.dump(ds, f)
PY
  bq --project_id="${PROJECT_ID}" update --source "${_ds_json}" \
    "${PROJECT_ID}:${BQ_DATASET}"
  rm -f "${_ds_json}"
fi

echo
echo "Done. Service account to share tracker sheets with:"
echo "  ${SA_EMAIL}"
