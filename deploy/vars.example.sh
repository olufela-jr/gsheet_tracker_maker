# Copy this file to vars.sh and fill in your values, then run the scripts.
# vars.sh is gitignored so environment-specific values stay out of git.

export PROJECT_ID="your-project-id"
export BILLING_ACCOUNT="XXXXXX-XXXXXX-XXXXXX"
export REGION="asia-south1"
export SERVICE_NAME="tracker-service"
export SA_NAME="tracker-runner"

# BigQuery audit log. bootstrap.sh creates the dataset and table and grants the
# service account access. Leave BQ_DATASET empty to deploy without logging.
export BQ_LOCATION="asia-south1"
export BQ_DATASET="tracker_registry"
export BQ_TABLE="trackers"

# Control sheets. Set after you create the master and template sheets. The
# service exposes the template id via get_config so the master Apps Script can
# read it centrally.
export MASTER_SHEET_ID=""
export TEMPLATE_SHEET_ID=""

# Access control. ALLOWED_EMAILS is the small list of who may use the system at
# all (comma-separated). ALLOWED_DOMAIN optionally allows any address in a
# Workspace domain. ADMIN_EMAILS may act on any tracker (others only on ones
# they created). RATE_LIMIT_PER_MIN is a per-caller cap (0 disables).
export ALLOWED_EMAILS=""
export ADMIN_EMAILS=""
export ALLOWED_DOMAIN=""
export RATE_LIMIT_PER_MIN="30"
