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

export SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
export REPO_ROOT="$(cd "${_here}/.." && pwd)"
