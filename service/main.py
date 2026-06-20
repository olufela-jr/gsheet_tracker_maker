"""HTTP entry point for the tracker generator service.

The service is private; callers reach it through the relay (a standalone Apps
Script web app), which forwards the request body and authenticates to Cloud Run
with its single stable identity. Every request body carries the caller's Google
identity token, which this service verifies (signature, not audience) to get the
caller email, then gates on an allowlist, a per-caller rate limit, and, for sheet
actions, per-tracker ownership from the BigQuery registry.

POST / with a JSON body, get back {"status": "ok|error", "message", "detail"}.

  {"token": "<caller id token>", "action": "...", "spreadsheet_id": "...", ...}

Actions: run_all, validate, generate_mapping, create_named_ranges,
build_frontend (need a spreadsheet_id and tracker ownership); scaffold (creates
input tabs + BigQuery row, takes url/title/client/sub_brand); list_actions and
get_config (no sheet, allowlist only).

The service is stateless. Each request builds a fresh client, runs one action,
and returns.
"""

import json
import os
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from googleapiclient.errors import HttpError

import auth
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


def _audit(caller, action, spreadsheet_id, result, **extra):
    """Emit one structured JSON audit line (captured by Cloud Logging)."""
    record = {
        "audit": True,
        "caller": caller,
        "action": action,
        "spreadsheet_id": spreadsheet_id,
        "result": result,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    record.update(extra)
    print(json.dumps(record), flush=True)


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


def _scaffold(payload, caller):
    """Scaffold a newly created sheet and log its metadata to BigQuery."""
    required = ["spreadsheet_id", "title", "client", "sub_brand"]
    missing = [field for field in required if not payload.get(field)]
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
            # created_by is the verified caller, not a value the client asserts.
            created_by=caller,
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
    cfg = DEFAULT_CONFIG

    # Authenticate the caller from the token the relay forwarded, then gate on
    # the allowlist and the per-caller rate limit.
    try:
        caller = auth.verify_caller(payload.get("token"))
        if not auth.is_allowed(caller, cfg):
            raise auth.AuthError("Caller not allowed", 403)
        auth.check_rate_limit(caller, cfg.rate_limit_per_min)
    except auth.AuthError as exc:
        _audit(getattr(exc, "email", None), action, payload.get("spreadsheet_id"),
               "denied", reason=exc.message)
        return _error(exc.message, code=exc.code)

    # Actions that need no sheet (allowlisted callers only, already checked).
    if action == "list_actions":
        return jsonify(status="ok", message="ok", detail={"actions": MENU_ACTIONS})

    if action == "get_config":
        return jsonify(
            status="ok",
            message="ok",
            detail={
                "master_sheet_id": cfg.master_sheet_id,
                "template_sheet_id": cfg.template_sheet_id,
            },
        )

    if action == "scaffold":
        _audit(caller, action, payload.get("spreadsheet_id"), "start")
        return _scaffold(payload, caller)

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

    # Per-spreadsheet authorization: a caller may act only on a tracker they
    # created, unless they are an admin.
    if not auth.is_admin(caller, cfg):
        owner = None
        if cfg.bigquery_dataset:
            owner = BigQueryClient(cfg.bigquery_project).created_by(
                cfg.bigquery_dataset, cfg.bigquery_table, spreadsheet_id
            )
        if not owner or owner.lower() != caller:
            _audit(caller, action, spreadsheet_id, "denied", reason="not owner")
            return _error("Not authorized for this tracker", code=403)

    _audit(caller, action, spreadsheet_id, "start")
    client = SheetsClient(spreadsheet_id)
    return _run(
        lambda: SHEET_ACTIONS[action](client, cfg),
        "{} completed".format(action),
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
