"""Tests for the request-handling helpers in main.

Focused on error mapping: a failed action's cause has to reach the operator,
since the JSON response is the only thing the control sheet ever sees.
"""

import json

import pytest
from googleapiclient.errors import HttpError

# main imports the BigQuery client at module scope, so these run only where the
# full requirements are installed (the container and CI), not a bare checkout.
main = pytest.importorskip(
    "main", reason="google-cloud-bigquery not installed", exc_type=ImportError
)


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
