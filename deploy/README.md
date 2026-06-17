# Deploy

Parameterized deploy for the tracker service. Environment-specific values live
in `vars.sh` (gitignored); everything else is committed so the infra steps are
auditable.

## Setup

1. Copy the template and fill it in:
   ```sh
   cp vars.example.sh vars.sh
   # edit vars.sh
   ```
2. Make sure the right gcloud account is active:
   ```sh
   gcloud config set account YOUR_EMAIL
   ```

## Order

```sh
./bootstrap.sh          # one time: project, billing, APIs, SA, BigQuery audit log
./deploy.sh             # build and deploy (repeatable)
```

## BigQuery audit log

`bootstrap.sh` creates the dataset and table (`BQ_DATASET.BQ_TABLE`) and grants
the service account `bigquery.dataEditor` on that dataset only. Every tracker
created appends one row:

```
event_id | created_at | spreadsheet_id | url | title | client | sub_brand | created_by | status | service_revision
```

To deploy without logging, leave `BQ_DATASET` empty in `vars.sh`.

## Smoke test

```sh
./test.sh proxy                  # terminal A: authenticated proxy on :8080
./test.sh validate  <SHEET_ID>   # terminal B
./test.sh run_all   <SHEET_ID>
./test.sh scaffold  <SHEET_ID>
```

The target sheet must be shared as Editor with the service account.

## Files

- `vars.example.sh`  committed template
- `vars.sh`          real values, gitignored
- `_load.sh`         shared loader (sourced, not run directly)
- `bootstrap.sh`     one-time project + service account setup
- `deploy.sh`        build and deploy to Cloud Run
- `test.sh`          proxy and curl smoke tests
