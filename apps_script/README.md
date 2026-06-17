# Apps Script (master + template)

Two bound scripts, managed with [clasp](https://github.com/google/clasp) so the
code and the sheet formatting live in this repo and can be pushed to any
instance.

- `master/` - the control sheet. `New tracker` copies the template, shares the
  service account, and registers the tracker. `setupMaster()` formats the panel.
- `template/` - the sheet children are copied from. `Code.gs` is the generic
  dispatcher shim every child carries; `setupTemplate()` formats the input tabs.

`.clasp.json` (the link to a specific sheet's script) is gitignored, because it
is per-instance. The committed source is the same everywhere.

## One-time setup

```sh
npm install -g @google/clasp
clasp login
```

Also enable the Apps Script API once at https://script.google.com/home/usersettings

## Link and push each script

Run once per instance, with the two blank sheet ids. `--parentId` binds the
script to that sheet.

```sh
# Master
cd apps_script/master
clasp create-script --type sheets --title "Tracker Admin" --parentId <MASTER_SHEET_ID> --rootDir .
clasp push --force

# Template
cd ../template
clasp create-script --type sheets --title "Tracker Template" --parentId <TEMPLATE_SHEET_ID> --rootDir .
clasp push --force
```

## Per-instance configuration (not in the repo)

Config lives in a `Config.gs` file in each folder, not in Script Properties,
because Script Properties do not copy when a sheet is copied but script code
does. `Config.gs` is gitignored and pushed by clasp; it is copied into every
child, so children inherit `SERVICE_URL` automatically.

- `master/Config.gs`: `var SERVICE_URL`, `var SERVICE_ACCOUNT_EMAIL`, `var TEMPLATE_SHEET_ID`
- `template/Config.gs`: `var SERVICE_URL`

Push after editing (`clasp push --force`), then apply formatting once:

- master: open it, Tracker Admin > Apply formatting (or Run > `setupMaster`)
- template: Apps Script editor > Run > `setupTemplate`

After this, push changes anytime with `clasp push -f` from the folder; redeploy
the Cloud Run service for logic changes.
