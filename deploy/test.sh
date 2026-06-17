#!/usr/bin/env bash
#
# Smoke test the deployed service through the authenticated Cloud Run proxy,
# which sidesteps token wrangling during development.
#
# Usage:
#   ./test.sh proxy                     # start the proxy on localhost:8080
#   ./test.sh validate  <SHEET_ID>      # validate an existing sheet
#   ./test.sh run_all   <SHEET_ID>      # full build on an existing sheet
#   ./test.sh scaffold  <SHEET_ID>      # scaffold a blank sheet + log it
#
# The sheet must be shared as Editor with the service account printed by
# bootstrap.sh.

source "$(dirname "$0")/_load.sh"

cmd="${1:-}"

if [ "${cmd}" = "proxy" ]; then
  exec gcloud run services proxy "${SERVICE_NAME}" --region "${REGION}"
fi

sheet_id="${2:-}"
if [ -z "${cmd}" ] || [ -z "${sheet_id}" ]; then
  echo "Usage: ./test.sh <action> <SHEET_ID>   (or: ./test.sh proxy)" >&2
  exit 1
fi

if [ "${cmd}" = "scaffold" ]; then
  payload=$(printf '{"action":"scaffold","spreadsheet_id":"%s","url":"https://docs.google.com/spreadsheets/d/%s/edit","title":"Test Tracker","client":"Test Client","sub_brand":"Test Sub-brand","created_by":"%s"}' \
    "${sheet_id}" "${sheet_id}" "$(gcloud config get-value account 2>/dev/null)")
else
  payload=$(printf '{"spreadsheet_id":"%s","action":"%s"}' "${sheet_id}" "${cmd}")
fi

curl -sS -X POST localhost:8080 -H 'Content-Type: application/json' -d "${payload}"
echo
