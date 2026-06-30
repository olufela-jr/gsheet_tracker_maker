# Apps Script (master only)

One script, managed with [clasp](https://github.com/google/clasp). It is the
ONLY script in the system - trackers carry no script at all.

- `master/` - the control sheet. `New tracker` creates a clean sheet (the
  operator owns it), shares the service account, and has the service scaffold +
  format it. `Operate on tracker` points the service at any sheet by URL and
  runs an action. `setupMaster()` formats the control panel.

Trackers are pure data: no bound script, no per-sheet authorization, nothing to
keep in sync.

`.clasp.json` (the link to the master's script) and `Config.gs` (per-instance
config) are gitignored. The committed source is the same everywhere.

## One-time setup

```sh
npm install -g @google/clasp
clasp login
```

Enable the Apps Script API once at https://script.google.com/home/usersettings

## Link and push the master

Use `--parentId` to bind to the existing master sheet (NOT `--type sheets`,
which makes a new sheet). After `create-script`, clasp overwrites the local
`appsscript.json` with a default; restore the committed one before `push`.

```sh
cd apps_script/master
clasp create-script --parentId <MASTER_SHEET_ID> --rootDir .
clasp push --force
```

Authorize the master once (Run any function, accept the consent including the
external-request scope). That is the only authorization in the whole system.

## Per-instance configuration (Config.gs, gitignored, pushed by clasp)

- `master/Config.gs`: `var SERVICE_URL` (the private Cloud Run URL),
  `var SERVICE_ACCOUNT_EMAIL`

Apply formatting once: `Tracker Admin > Apply formatting`. Push code changes
anytime with `clasp push --force`; redeploy the Cloud Run service for logic
changes.
