# Apps Script (relay + master + template)

Three scripts, managed with [clasp](https://github.com/google/clasp) so the code
and the sheet formatting live in this repo and can be pushed to any instance.

- `relay/` - a standalone web app. A dumb pass-through: it attaches its own
  identity token and forwards the request to the private Cloud Run service. It
  is the one stable identity the service accepts, so children do not need
  per-child audience registration. No business logic, so it never changes.
- `master/` - the control sheet. `New tracker` copies the template, shares the
  service account, and registers the tracker. `setupMaster()` formats the panel.
- `template/` - the sheet children are copied from. `Code.gs` is the generic
  dispatcher shim every child carries; `setupTemplate()` formats the input tabs.

Children and the master call the relay, not the service directly.

`.clasp.json` (the link to a specific script) and `Config.gs` (per-instance
config) are gitignored. The committed source is the same everywhere.

## One-time setup

```sh
npm install -g @google/clasp
clasp login
```

Enable the Apps Script API once at https://script.google.com/home/usersettings

## Link and push each script

Use `--parentId` to bind to an existing sheet (NOT `--type sheets`, which makes a
new sheet). The relay is standalone (no parent). After `create-script`, clasp
overwrites the local `appsscript.json` with a default; restore the committed one
before `clasp push --force`.

```sh
# Relay (standalone web app)
cd apps_script/relay
clasp create-script --title "Tracker Relay" --rootDir .
clasp push --force
clasp deploy            # creates a web app deployment; note the /exec URL

# Master
cd ../master
clasp create-script --parentId <MASTER_SHEET_ID> --rootDir .
clasp push --force

# Template
cd ../template
clasp create-script --parentId <TEMPLATE_SHEET_ID> --rootDir .
clasp push --force
```

## Per-instance configuration (Config.gs, gitignored, pushed by clasp)

- `relay/Config.gs`: `var SERVICE_URL` (the private Cloud Run URL)
- `master/Config.gs`: `var RELAY_URL`, `var SERVICE_ACCOUNT_EMAIL`, `var TEMPLATE_SHEET_ID`
- `template/Config.gs`: `var RELAY_URL` (copied into every child)

`RELAY_URL` is the relay web app `/exec` URL from `clasp deploy`. Set it after
the relay is deployed, then `clasp push --force` the master and template.

Apply formatting once: master `Tracker Admin > Apply formatting`; template
`Run > setupTemplate`.

After this, push code changes anytime with `clasp push --force`; redeploy the
Cloud Run service for logic changes.
