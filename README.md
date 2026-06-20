# Google Sheets Performance Tracker Generator

Generates a performance tracker inside a Google Sheet. The generation logic
runs as a Python service on Cloud Run. The sheet keeps a custom menu, and a
thin Apps Script shim calls the service. Nothing is generated inside Apps
Script, and the service holds no state between requests.

## How it fits together

```
Master control sheet  ── New tracker ─┐
  (bound Apps Script)                 │ copy template (as user)
                                      v
Child tracker sheet  ── Refresh ──────┐   owned by the user, carries a
  (generic shim)                      │   tiny dispatcher shim
                                      v
Relay web app  ── forwards (+ its own identity) ──> Cloud Run (PRIVATE)
  (dumb pass-through)                                Python service
                                                     all logic + auth here
                                                     ──> Sheets + BigQuery
```

- **Cloud Run is the only place logic and access control live.** Callers send a
  caller identity token, a spreadsheet id, and an action; the service verifies
  the caller, authorizes, and does the work. To advance behaviour you redeploy
  the service, and every existing sheet picks it up untouched.
- The service stays **private**. Children and the master call the **relay** (a
  standalone Apps Script web app); the relay holds one stable identity and is
  the only thing Cloud Run accepts, so unlimited children work without per-child
  setup. The service verifies the forwarded caller token, checks an allowlist,
  rate-limits, and enforces per-tracker ownership from the BigQuery registry.
- The service runs as a service account that is an Editor on each child sheet.

### The pieces

- **Relay** ([apps_script/relay/](apps_script/relay/)) is a dumb forwarder: it
  attaches its identity and passes requests to the private service. No logic.
- **Master control sheet** ([apps_script/master/](apps_script/master/)) is where
  users mint trackers. "New tracker" copies the template (as the clicking user,
  so the user owns the child), shares the service account, and calls `scaffold`
  to set up the input tabs and log the tracker to BigQuery.
- **Template sheet** ([apps_script/template/](apps_script/template/)) has empty
  `setup`/`data_source` tabs and a bound generic dispatcher shim. Children are
  copies of it.
- **Child sheets** carry only the shim: `Refresh` (runs `run_all`) and
  `More actions...` (a server-driven picker from `list_actions`). The shim holds
  no logic, so children never need editing; new features ship server-side.

## The sheet layout

Four tabs. Tab names are constants in [service/config.py](service/config.py).
Two are inputs the user fills in (`setup`, `data_source`); two are generated
(`mapping`, `frontend`). Tab matching is case-insensitive, so `setup` and
`Setup` are the same tab.

- **setup** (input) declares the schema. Column A is the field name (must match
  a data_source header exactly), column B is the type (`metric` or
  `dimension`). Row 1 is a header and is skipped.
- **data_source** (input) is the raw data. Row 1 is headers, row 2+ is data.
- **mapping** (generated) has one column per dimension: row 1 the dimension
  name, row 2 the `**` sentinel (meaning "All"), row 3+ the distinct values.
- **frontend** (generated) is the themed dashboard. Row 1 dimension labels,
  row 2 dropdowns (default `**`), row 4 the metric header, row 5+ the metric
  tiles with SUMIFS values.

## Actions

Two request shapes hit the same `POST /` endpoint.

Operate on an existing sheet (`{"spreadsheet_id": "...", "action": "..."}`):

- `validate` reads setup and data_source headers. Errors if there are no
  metrics, no dimensions, or any declared field is missing from the headers.
- `generate_mapping` ensures the mapping tab exists, then writes one column per
  dimension with its distinct sorted values (mapping is cleared first).
- `create_named_ranges` creates one named range per data_source column,
  pointing at `'data_source'!<col>2:<col>`. Existing names are skipped.
- `build_frontend` ensures the frontend tab exists, then rebuilds the themed
  dashboard: a banner, filter dropdowns, and the metrics as SUMIFS tiles.
- `run_all` runs all of the above in order.

Scaffold a freshly created sheet (`{"action": "scaffold", "spreadsheet_id":
"...", "url": "...", "title": "...", "client": "...", "sub_brand": "...",
"created_by": "..."}`):

- `scaffold` ensures the two input tabs (`setup`, `data_source`) exist on a
  sheet Apps Script just created, seeding the setup header only when it creates
  that tab. The generated tabs (mapping, frontend) are created later by the
  generation steps. The default sheet is renamed rather than deleted, and any
  other sheets the user already has are left alone. `spreadsheet_id`, `title`,
  `client`, `sub_brand`, and `created_by` are required. If a BigQuery dataset
  is configured, one audit row is streamed in (`event_id`, `created_at`,
  `spreadsheet_id`, `url`, `title`, `client`, `sub_brand`, `created_by`,
  `status`, `service_revision`); the response `detail` reports `logged` and any
  `logging_error`.

No sheet needed:

- `list_actions` returns the actions (name + label) the child shim offers in its
  "More actions" menu. Adding an action here means no child sheet needs editing.
- `get_config` returns the `master_sheet_id` and `template_sheet_id`, so the
  master Apps Script can read the template id from the service rather than
  hardcoding it.

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

### 5. Allow the sheet editors to invoke the service

`ScriptApp.getIdentityToken()` mints a token for the user who clicks the menu.
That user's Google identity must have the `run.invoker` role on the service.
Grant it to each user (or a group containing them):

```sh
gcloud run services add-iam-policy-binding tracker-service \
  --region REGION \
  --member "user:someone@example.com" \
  --role roles/run.invoker
```

### 6. Create the template sheet

Make one spreadsheet to be the template. Open Extensions > Apps Script, add the
files from [apps_script/template/](apps_script/template/) (`Code.gs` and the
`appsscript.json` manifest), and set one Script Property:

- `SERVICE_URL` - the Cloud Run URL from step 4

This property is copied with the template, so every child inherits it. Share
the template so the people who will create trackers can copy it, and note its
file id.

### 7. Create the master control sheet

Make another spreadsheet to be the control panel. Open Extensions > Apps Script,
add the files from [apps_script/master/](apps_script/master/) (`Menu.gs`,
`Service.gs`, `appsscript.json`), and set Script Properties:

- `SERVICE_URL` - the Cloud Run URL from step 4
- `SERVICE_ACCOUNT_EMAIL` - the service account from step 1, shared as editor on
  each child
- `TEMPLATE_SHEET_ID` - the template file id from step 6 (optional if the
  service has `TEMPLATE_SHEET_ID` set; the master reads it via `get_config`)

Put the master and template ids into `deploy/vars.sh` (`MASTER_SHEET_ID`,
`TEMPLATE_SHEET_ID`) and redeploy so `get_config` serves them.

Reload the master. The **Tracker Admin** menu appears. **New tracker** prompts
for client / sub-brand / title, copies the template (you own the copy), shares
the service account, and registers it. In the child, fill `setup` +
`data_source`, then **Tracker > Refresh** generates `mapping` and `frontend`.

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
  theme.py           Frontend palette, layout, and formatting requests
  config.py          tab names, sentinel, sanitise + helpers, ids, allowlist
  requirements.txt
  Dockerfile
  .dockerignore
  tests/             pytest for the pure helpers
apps_script/
  relay/             standalone web app: dumb forwarder to the private service
    Code.gs  appsscript.json
  master/            control sheet: creates trackers from the template
    Menu.gs  Service.gs  Setup.gs  appsscript.json
  template/          child shim children are copied from
    Code.gs  Setup.gs  appsscript.json
deploy/              parameterized deploy (bootstrap, deploy, test, vars)
  README.md          deploy instructions
```
