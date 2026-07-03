# Shared helper sourced by the other scripts. Loads vars.sh and derives the
# service account email. Not meant to be run directly.

set -euo pipefail

_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -f "${_here}/vars.sh" ]; then
  echo "vars.sh not found. Copy vars.example.sh to vars.sh and fill it in." >&2
  exit 1
fi

# shellcheck disable=SC1091
source "${_here}/vars.sh"

# Target the configured project (and account, if set) for THIS run only, via
# gcloud env vars. This overrides the active gcloud config without changing it,
# so switching projects/accounts elsewhere never misroutes a deploy.
export CLOUDSDK_CORE_PROJECT="${PROJECT_ID}"
if [ -n "${GCLOUD_ACCOUNT:-}" ]; then
  export CLOUDSDK_CORE_ACCOUNT="${GCLOUD_ACCOUNT}"
fi
echo "Targeting project ${PROJECT_ID}${GCLOUD_ACCOUNT:+ as ${GCLOUD_ACCOUNT}}"

export SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
export REPO_ROOT="$(cd "${_here}/.." && pwd)"
