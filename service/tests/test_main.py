"""Tests for the HTTP entry point.

Covers the auth and per-tracker ownership gating, and the error mapping that
carries a failed action's cause to the operator — the JSON response is the
only thing the control sheet ever sees. Everything past the gate is faked:
no Sheets calls, no BigQuery, no network.
"""

import json

import pytest

# main imports flask, the google api client, and the BigQuery client at module
# scope, so these run only where the full requirements are installed (the
# container and CI), not a bare checkout.
main = pytest.importorskip(
    "main", reason="service requirements not installed", exc_type=ImportError
)

from googleapiclient.errors import HttpError  # noqa: E402

from config import Config  # noqa: E402


class FakeResp:
    """Stands in for the httplib2 response an HttpError carries."""

    def __init__(self, status):
        self.status = status
        self.reason = "Bad Request"


def http_error(status, message):
    content = json.dumps({"error": {"code": status, "message": message}})
    return HttpError(FakeResp(status), content.encode("utf-8"))


class TestSheetsReason:
    def test_pulls_the_api_message_out_of_the_body(self):
        exc = http_error(400, "Invalid requests[3].setDataValidation: no grid")
        assert main._sheets_reason(exc) == (
            "Invalid requests[3].setDataValidation: no grid"
        )

    def test_falls_back_to_the_wrapper_when_body_is_not_json(self):
        exc = HttpError(FakeResp(500), b"<html>nope</html>")
        assert "HttpError" in main._sheets_reason(exc)

    def test_falls_back_when_body_has_no_message(self):
        exc = HttpError(FakeResp(400), json.dumps({"error": {}}).encode())
        assert "HttpError" in main._sheets_reason(exc)


def body_of(response):
    payload, _code = response
    return json.loads(payload.get_data(as_text=True))


class TestRunErrorMapping:
    def _run(self, work):
        with main.app.app_context():
            return main._run(work, "done")

    def test_success_returns_ok_and_the_detail(self):
        with main.app.app_context():
            response = main._run(lambda: {"tabs": 3}, "run_all completed")
        payload = json.loads(response.get_data(as_text=True))
        assert payload["status"] == "ok"
        assert payload["message"] == "run_all completed"
        assert payload["detail"] == {"tabs": 3}

    def test_sheets_error_carries_the_reason_in_errors(self):
        def work():
            raise http_error(400, "Range exceeds grid limits")

        payload = body_of(self._run(work))
        assert payload["status"] == "error"
        # The status code narrows it down; the reason is what is actionable.
        assert payload["message"] == "Sheets API error (400)"
        assert payload["detail"]["errors"] == ["Range exceeds grid limits"]

    def test_sheets_error_returns_502(self):
        def work():
            raise http_error(429, "Quota exceeded")

        _payload, code = self._run(work)
        assert code == 502

    def test_validation_error_keeps_its_own_shape(self):
        def work():
            raise main.tracker.ValidationError(["setup is empty"])

        payload, code = self._run(work)
        assert code == 400
        assert json.loads(payload.get_data(as_text=True))["detail"]["errors"] == [
            "setup is empty"
        ]

    def test_unexpected_error_also_lands_in_errors(self):
        def work():
            raise RuntimeError("boom")

        payload, code = self._run(work)
        assert code == 500
        body = json.loads(payload.get_data(as_text=True))
        assert body["message"] == "boom"
        assert body["detail"]["errors"] == ["boom"]


class FakeBigQuery:
    """Stands in for BigQueryClient. owner is what created_by returns; set
    raises to make the lookup fail the way an outage or a missing role would."""

    def __init__(self, owner=None, raises=None):
        self.owner = owner
        self.raises = raises
        self.inserted = []

    def created_by(self, dataset, table, spreadsheet_id):
        if self.raises:
            raise self.raises
        return self.owner

    def insert_row(self, dataset, table, row):
        self.inserted.append(row)


@pytest.fixture
def env(monkeypatch):
    """Wire handle() up to fakes and return a small harness."""

    class Harness:
        def __init__(self):
            self.bq = FakeBigQuery()
            self.ran = []
            self.caller = "owner@x.com"

    h = Harness()

    monkeypatch.setattr(main.auth, "verify_caller", lambda token: h.caller)
    monkeypatch.setattr(main, "SheetsClient", lambda sid: object())
    monkeypatch.setattr(main.tracker, "require_input_tabs", lambda c, cfg: None)
    monkeypatch.setattr(main, "BigQueryClient", lambda project=None: h.bq)
    monkeypatch.setattr(
        main, "SHEET_ACTIONS", {"validate": lambda c, cfg: h.ran.append("validate")}
    )
    monkeypatch.setattr(
        main,
        "DEFAULT_CONFIG",
        Config(
            allowed_emails="owner@x.com, other@x.com, boss@x.com",
            admin_emails="boss@x.com",
            bigquery_dataset="ds",
            bigquery_table="trackers",
            rate_limit_per_min=0,
        ),
    )
    return h


def post(**body):
    body.setdefault("token", "t")
    with main.app.test_client() as client:
        response = client.post("/", json=body)
        return response.status_code, response.get_json()


def audit_lines(capsys):
    lines = []
    for line in capsys.readouterr().out.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("audit"):
            lines.append(record)
    return lines


class TestOwnership:
    def test_owner_may_act(self, env):
        env.bq.owner = "owner@x.com"
        status, _ = post(action="validate", spreadsheet_id="S1")
        assert status == 200
        assert env.ran == ["validate"]

    def test_owner_match_ignores_case(self, env):
        env.bq.owner = "Owner@X.com"
        status, _ = post(action="validate", spreadsheet_id="S1")
        assert status == 200

    def test_non_owner_denied(self, env):
        env.bq.owner = "someone-else@x.com"
        status, body = post(action="validate", spreadsheet_id="S1")
        assert status == 403
        assert env.ran == []
        assert "Not authorized" in body["message"]

    def test_admin_bypasses_ownership(self, env):
        env.caller = "boss@x.com"
        env.bq.owner = "someone-else@x.com"
        status, _ = post(action="validate", spreadsheet_id="S1")
        assert status == 200
        assert env.ran == ["validate"]

    def test_admin_still_has_to_be_on_the_allowlist(self, env, monkeypatch):
        # ADMIN_EMAILS widens what a caller may touch; it does not admit them.
        monkeypatch.setattr(
            main, "DEFAULT_CONFIG", Config(allowed_emails="owner@x.com",
                                           admin_emails="boss@x.com",
                                           bigquery_dataset="ds",
                                           rate_limit_per_min=0)
        )
        env.caller = "boss@x.com"
        status, _ = post(action="validate", spreadsheet_id="S1")
        assert status == 403

    def test_unowned_sheet_is_claimed_then_runs(self, env):
        env.bq.owner = None
        status, _ = post(action="validate", spreadsheet_id="S1")
        assert status == 200
        assert env.ran == ["validate"]
        assert [r["created_by"] for r in env.bq.inserted] == ["owner@x.com"]

    def test_lookup_failure_denies_rather_than_falling_open(self, env, capsys):
        # An authorization check that could not run has not passed: the action
        # must not execute, and the sheet must not be silently re-claimed.
        env.bq.raises = RuntimeError("bigquery is down")
        status, body = post(action="validate", spreadsheet_id="S1")
        assert status == 503
        assert env.ran == []
        assert env.bq.inserted == []
        denied = [r for r in audit_lines(capsys) if r["result"] == "denied"]
        assert denied and "ownership lookup failed" in denied[0]["reason"]

    def test_missing_registry_is_audited(self, env, capsys, monkeypatch):
        monkeypatch.setattr(
            main, "DEFAULT_CONFIG", Config(allowed_emails="owner@x.com",
                                           bigquery_dataset="", rate_limit_per_min=0)
        )
        status, _ = post(action="validate", spreadsheet_id="S1")
        assert status == 200  # local and test runs still work
        results = [r["result"] for r in audit_lines(capsys)]
        assert "ownership_unenforced" in results

    def test_claim_failure_is_audited(self, env, capsys):
        env.bq.owner = None
        env.bq.insert_row = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("nope"))
        status, _ = post(action="validate", spreadsheet_id="S1")
        assert status == 200  # claiming is best-effort, the action still runs
        results = [r["result"] for r in audit_lines(capsys)]
        assert "claim_failed" in results


class TestDenialAudit:
    def test_denied_caller_is_named_in_the_audit_line(self, env, capsys):
        env.caller = "intruder@other.com"  # verifies fine, not on the allowlist
        status, _ = post(action="validate", spreadsheet_id="S1")
        assert status == 403
        denied = [r for r in audit_lines(capsys) if r["result"] == "denied"]
        assert denied and denied[0]["caller"] == "intruder@other.com"

    def test_rate_limited_caller_is_named(self, env, capsys, monkeypatch):
        monkeypatch.setattr(
            main, "DEFAULT_CONFIG", Config(allowed_emails="owner@x.com",
                                           bigquery_dataset="", rate_limit_per_min=1)
        )
        post(action="list_actions")
        capsys.readouterr()
        status, _ = post(action="list_actions")
        assert status == 429
        denied = [r for r in audit_lines(capsys) if r["result"] == "denied"]
        assert denied and denied[0]["caller"] == "owner@x.com"

    def test_unverifiable_token_has_no_caller_to_log(self, env, capsys, monkeypatch):
        def boom(token):
            raise main.auth.AuthError("Invalid identity token", 401)

        monkeypatch.setattr(main.auth, "verify_caller", boom)
        status, _ = post(action="validate", spreadsheet_id="S1")
        assert status == 401
        denied = [r for r in audit_lines(capsys) if r["result"] == "denied"]
        assert denied and denied[0]["caller"] is None
