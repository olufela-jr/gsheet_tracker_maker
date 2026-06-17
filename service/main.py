"""HTTP entry point for the tracker generator service.

POST / with a JSON body and get back
{"status": "ok|error", "message": "...", "detail": {...}}.

Two request shapes:

  Operate on an existing sheet:
    {"spreadsheet_id": "...", "action": "run_all|validate|generate_mapping|
                                          create_named_ranges|build_frontend"}

  Scaffold a sheet Apps Script just created (the user owns it), and log its
  metadata to BigQuery:
    {"action": "scaffold", "spreadsheet_id": "...", "url": "...",
     "title": "...", "client": "...", "sub_brand": "...", "created_by": "..."}

  No sheet needed:
    {"action": "list_actions"}  -> the child menu's available actions
    {"action": "get_config"}    -> master and template sheet ids

The service is stateless. Each request builds a fresh client, runs one action,
and returns.
"""

import os
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from googleapiclient.errors import HttpError

import tracker
from bq_client import BigQueryClient
from config import DEFAULT_CONFIG
from sheets_client import SheetsClient

app = Flask(__name__)

# Actions that operate on an existing spreadsheet, keyed by name.
SHEET_ACTIONS = {
    "validate": tracker.validate,
    "generate_mapping": tracker.generate_mapping,
    "create_named_ranges": tracker.create_named_ranges,
    "build_frontend": tracker.build_frontend,
    "run_all": tracker.run_all,
}

# The actions a child sheet's "More actions" menu offers, with display labels.
# The shim fetches this list so new actions need no child edit. Order matters.
MENU_ACTIONS = [
    {"action": "run_all", "label": "Refresh (run all)"},
    {"action": "validate", "label": "Validate setup"},
    {"action": "generate_mapping", "label": "Generate mapping"},
    {"action": "create_named_ranges", "label": "Create named ranges"},
    {"action": "build_frontend", "label": "Build frontend"},
]


def _error(message, detail=None, code=400):
    return jsonify(status="error", message=message, detail=detail or {}), code


def _run(work, ok_message):
    """Run a unit of work and map any failure to a JSON error response."""
    try:
        detail = work()
        return jsonify(status="ok", message=ok_message, detail=detail or {})
    except tracker.ValidationError as exc:
        return _error("Validation failed", {"errors": exc.errors}, 400)
    except HttpError as exc:
        return _error("Sheets API error", {"error": str(exc)}, 502)
    except Exception as exc:  # noqa: BLE001 - return any other failure as JSON
        return _error(str(exc), {}, 500)


@app.get("/healthz")
def healthz():
    return jsonify(status="ok", message="alive", detail={})


# Fields the caller must supply on scaffold, so every tracker is logged with
# the metadata the audit log needs.
SCAFFOLD_REQUIRED = ["spreadsheet_id", "title", "client", "sub_brand", "created_by"]


def _scaffold(payload):
    """Scaffold a newly created sheet and log its metadata to BigQuery."""
    missing = [field for field in SCAFFOLD_REQUIRED if not payload.get(field)]
    if missing:
        return _error("Missing fields: " + ", ".join(missing), code=400)

    cfg = DEFAULT_CONFIG
    spreadsheet_id = payload["spreadsheet_id"]
    client = SheetsClient(spreadsheet_id)

    def work():
        detail = tracker.scaffold(client, cfg)
        detail["logged"] = False
        if not cfg.bigquery_dataset:
            return detail
        record = tracker.build_tracker_record(
            event_id=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc).isoformat(),
            spreadsheet_id=spreadsheet_id,
            url=payload.get("url", ""),
            title=payload.get("title", ""),
            client=payload.get("client", ""),
            sub_brand=payload.get("sub_brand", ""),
            created_by=payload.get("created_by", ""),
            status="active",
            # Cloud Run injects K_REVISION; blank when running locally.
            service_revision=os.environ.get("K_REVISION", ""),
        )
        try:
            tracker.log_tracker(BigQueryClient(cfg.bigquery_project), cfg, record)
            detail["logged"] = True
        except Exception as exc:  # noqa: BLE001 - the sheet exists; surface but do not fail
            detail["logging_error"] = str(exc)
        return detail

    return _run(work, "scaffold completed")


@app.post("/")
def handle():
    payload = request.get_json(silent=True) or {}
    action = payload.get("action")

    if action == "list_actions":
        # Drives the child shim's "More actions" menu. No sheet needed.
        return jsonify(status="ok", message="ok", detail={"actions": MENU_ACTIONS})

    if action == "get_config":
        # Lets the master Apps Script read the template id centrally rather
        # than hardcoding it. No sheet needed.
        cfg = DEFAULT_CONFIG
        return jsonify(
            status="ok",
            message="ok",
            detail={
                "master_sheet_id": cfg.master_sheet_id,
                "template_sheet_id": cfg.template_sheet_id,
            },
        )

    if action == "scaffold":
        return _scaffold(payload)

    spreadsheet_id = payload.get("spreadsheet_id")
    if not spreadsheet_id:
        return _error("Missing spreadsheet_id", code=400)
    if action not in SHEET_ACTIONS:
        return _error(
            "Unknown action '{}'".format(action),
            {
                "actions": sorted(SHEET_ACTIONS.keys())
                + ["scaffold", "list_actions", "get_config"]
            },
            400,
        )

    client = SheetsClient(spreadsheet_id)
    return _run(
        lambda: SHEET_ACTIONS[action](client, DEFAULT_CONFIG),
        "{} completed".format(action),
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
