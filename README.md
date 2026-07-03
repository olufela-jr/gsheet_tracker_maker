# Google Sheets Performance Tracker Generator

Generates and maintains performance trackers in Google Sheets. All logic runs
as a Python service on Cloud Run. A single master sheet (the only Apps Script)
creates trackers and operates on them. Trackers themselves carry no script.

## How it fits together

```
Master control sheet (the ONLY Apps Script)
  - New tracker          create a clean sheet, hand it to the service
  - Operate on tracker   point the service at any sheet by URL
        |  operator identity token (audience registered; operator has run.invoker)
        v
  Cloud Run service (PRIVATE) - verify caller, allowlist, rate-limit,
        per-tracker ownership, audit ──> Sheets API + BigQuery
Trackers: pure data, no bound script.
```

- **Cloud Run is the only place logic and access control live.** The master
  sends the caller's identity token, a spreadsheet id, and an action; the
  service verifies the caller, authorizes, and does the work. To advance
  behaviour you redeploy the service; every tracker picks it up untouched.
- The service is **private**. The master (one script, one audience) calls it
  directly; the operator's token authenticates to Cloud Run (audience registered,
  operator has `run.invoker`) and is verified by the service to identify the
  caller. The service checks an allowlist, rate-limits, and enforces per-tracker
  ownership from the BigQuery registry.
- The service runs as a service account that is an Editor on each tracker.

### The pieces

- **Master control sheet** ([apps_script/master/](apps_script/master/)) is the
  only script. **New tracker** creates a clean sheet (the operator owns it),
  shares the service account, and calls `scaffold` to set up + format the input
  tabs and log the tracker to BigQuery. **Operate on tracker** points the service
  at any sheet by URL and runs an action.
- **Trackers** are pure data: no bound script, so no per-sheet authorization and
  nothing to keep in sync. They are freely copyable and shareable.

## The sheet layout

Tab names are constants in [service/config.py](service/config.py). Two are
inputs the user fills in (`setup`, `data_source`); the rest are generated
(`mapping`, `daily`, `weekly`, `monthly`). Tab matching is case-insensitive, so
`setup` and `Setup` are the same tab.

- **setup** (input) declares the schema. Column A is the field name, column B is
  the type (`metric`, `dimension`, or `date` - tag exactly one field `date`),
  column C is an optional `[Field]`-token formula for a calculated metric (for
  example `[Spend]/[Clicks]`), and column D is a number-format hint
  (`currency`, `percent`, `number`). Raw fields must match a data_source header
  exactly; calculated fields need not. Row 1 is a header and is skipped.
- **data_source** (input) is the raw data. Row 1 is headers, row 2+ is data.
- **mapping** (generated) has one column per dimension: row 1 the dimension
  name, row 2 the `**` sentinel (meaning "All"), row 3+ the distinct values.
- **daily / weekly / monthly** (generated) are the themed views. Each is a
  bucket x metric matrix: a filter bar (one dropdown per dimension, default
  `**`), a KPI strip of dimension-filtered grand totals, then one row per date
  bucket (day / week / month) and one column per metric. Calculated-metric
  columns are tinted periwinkle; the monthly view also carries a line chart of
  every metric over time.

## Actions

All requests `POST /` with the caller's identity token plus an action. Every
request is authenticated (token verified, allowlist, rate limit), and sheet
actions also require per-tracker ownership.

Operate on a sheet (`{"token": "...", "spreadsheet_id": "...", "action": "..."}`):

- `validate` reads setup and data_source headers. Errors if there are no
  metrics, no single `date` field, any raw field is missing from the headers,
  or a calculated field references an unknown or calculated field.
- `generate_mapping` ensures the mapping tab exists, then writes one column per
  dimension with its distinct sorted values (mapping is cleared first).
- `create_named_ranges` creates one named range per data_source column,
  pointing at `'data_source'!<col>2:<col>`. Existing names are skipped.
- `build_views` rebuilds the three view tabs (daily, weekly, monthly): a
  banner, filter dropdowns, a KPI strip, and a per-bucket SUMIFS matrix, plus a
  line chart on the monthly tab.
- `run_all` runs all of the above in order.

Scaffold a freshly created sheet (`{"token", "action": "scaffold",
"spreadsheet_id", "url", "title", "client", "sub_brand"}`):

- `scaffold` ensures the two input tabs (`setup`, `data_source`) exist, seeds and
  formats the setup header when it creates the tab, and leaves any tabs the user
  already has alone. `created_by` is the verified caller (not client-supplied).
  If BigQuery is configured, one audit row is streamed in (`event_id`,
  `created_at`, `spreadsheet_id`, `url`, `title`, `client`, `sub_brand`,
  `created_by`, `status`, `service_revision`); `detail` reports `logged` and any
  `logging_error`.

No sheet needed:

- `list_actions` returns the actions (name + label) the master's Operate picker
  offers. Adding an action here surfaces it with no Apps Script change.

## A gotcha worth knowing: columns are numbers and letters

The Sheets API addresses columns two different ways. Grid requests (named
ranges, data validation) use 0-based, half-open numeric indices
(`startColumnIndex`, `endColumnIndex`). A1 references in formulas and value
ranges use letters (`A`, `B`, ... `AA`). A named range and the SUMIFS formula
that points at it must agree, so both go through a single helper,
`column_to_letter` in [service/config.py](service/config.py), and named range names go
through a single `sanitise_name`. If you add code that builds a reference, use
those helpers rather than hand-rolling the conversion.

Two related details the service already handles:

- Formulas are written with the `USER_ENTERED` value input option so the Sheets
  API evaluates them. Plain labels are written `RAW`.
- The "All" case in SUMIFS uses `"<>"` (not equal to blank), not the `"*"`
  wildcard. `"*"` only matches text and would silently drop numeric and date
  rows.

## Setup

### 1. Create the service account

```sh
gcloud iam service-accounts create tracker-runner \
  --display-name "Tracker generator runtime"
```

Note its email, which looks like
`tracker-runner@PROJECT_ID.iam.gserviceaccount.com`.

### 2. Grant it Sheets access

The service uses Application Default Credentials, which on Cloud Run resolve to
the runtime service account. The account does not need a project-level Sheets
role. It only needs to be shared on each sheet (next step). Only the Sheets API
is required on the project; the service never touches Drive (Apps Script does
the file creation as the user):

```sh
gcloud services enable sheets.googleapis.com run.googleapis.com
```

### 3. Share each tracker sheet with the service account

Open the sheet, click Share, and add the service account email
(`tracker-runner@PROJECT_ID.iam.gserviceaccount.com`) as an **Editor**. Do this
for every sheet the tool should manage. This is what authorises the service to
read and write that sheet.

### 4. Deploy to Cloud Run

The deploy steps are scripted and parameterized in [deploy/](deploy/). Copy the
template, fill it in, and run the scripts:

```sh
cd deploy
cp vars.example.sh vars.sh   # then edit vars.sh with your project and billing
./bootstrap.sh               # one time: project, billing, APIs, SA, BigQuery log
./deploy.sh                  # build and deploy (private, runs as the SA)
```

`bootstrap.sh` covers step 1 (the service account) and creates the BigQuery
audit log table (`event_id, created_at, spreadsheet_id, url, title, client,
sub_brand, created_by, status, service_revision`), with `bigquery.dataEditor`
granted to the service account on that dataset only. To deploy without logging,
leave `BQ_DATASET` empty in `vars.sh`. See [deploy/README.md](deploy/README.md).

### 5. Set up the master control sheet

Create one blank sheet to be the master. With [clasp](https://github.com/google/clasp)
(`clasp login` + Apps Script API on), bind and push the master:

```sh
cd apps_script/master
clasp create-script --parentId <MASTER_SHEET_ID> --rootDir .
clasp push --force   # restore the committed appsscript.json first
```

Create `apps_script/master/Config.gs` (gitignored) with `var SERVICE_URL` (the
Cloud Run URL) and `var SERVICE_ACCOUNT_EMAIL`, push again, then
**Tracker Admin > Apply formatting**. Authorize the master once; that is the
only authorization in the system. See [apps_script/README.md](apps_script/README.md).

### 6. Register the master and the operators

The service stays private and accepts only the master's audience. Get the
master's audience (run a one-liner in the master editor; see SETUP.txt), then:

```sh
gcloud run services update tracker-service --region REGION --clear-custom-audiences
gcloud run services update tracker-service --region REGION \
  --add-custom-audiences=<master audience>
gcloud run services add-iam-policy-binding tracker-service --region REGION \
  --member "user:operator@example.com" --role roles/run.invoker
```

Set who may use it in `deploy/vars.sh` (`ALLOWED_EMAILS` / `ALLOWED_DOMAIN`,
`ADMIN_EMAILS`) and redeploy. Then **Tracker Admin > New tracker** creates a
clean tracker; fill its `setup` + `data_source` and **Operate on tracker** to
build it. Full runbook in [SETUP.txt](SETUP.txt).

## Local development

Run the unit tests (sanitisation, distinct value extraction, SUMIFS strings):

```sh
cd service
python -m pytest tests/ -q
```

Run the service locally (you will need Application Default Credentials with
access to the target sheet):

```sh
cd service
pip install -r requirements.txt
python main.py
# then: curl -X POST localhost:8080 -H 'Content-Type: application/json' \
#   -d '{"spreadsheet_id":"...","action":"validate"}'
```

## Project layout

```
service/             Cloud Run Python service
  main.py            Flask app: routes POST / to an action; auth gating
  auth.py            verify caller token, allowlist, admin, rate limit
  sheets_client.py   Sheets API wrapper over ADC
  bq_client.py       BigQuery wrapper (audit log + per-tracker created_by)
  tracker.py         domain logic plus the pure helpers
  theme.py           View palette, layout, and formatting requests
  config.py          tab names, sentinel, sanitise + helpers, ids, allowlist
  requirements.txt
  Dockerfile
  .dockerignore
  tests/             pytest for the pure helpers
apps_script/
  master/            the only script: creates trackers and operates on them
    Menu.gs  Service.gs  Setup.gs  appsscript.json
deploy/              parameterized deploy (bootstrap, deploy, test, vars)
  README.md          deploy instructions
```
