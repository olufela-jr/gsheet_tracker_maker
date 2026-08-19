"""Tests for the BigQuery wrapper's ownership lookup, with a fake client."""

import pytest

pytest.importorskip("google.cloud.bigquery")

from bq_client import BigQueryClient  # noqa: E402


class FakeQueryJob:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return self._rows


class FakeClient:
    """Records the SQL it is handed and replays canned rows."""

    project = "proj"

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.queries = []

    def query(self, query, job_config=None):
        self.queries.append((query, job_config))
        return FakeQueryJob(self.rows)


class TestCreatedBy:
    def test_returns_none_when_the_tracker_is_unknown(self):
        bq = BigQueryClient(client=FakeClient(rows=[]))
        assert bq.created_by("ds", "trackers", "S1") is None

    def test_returns_the_owner(self):
        bq = BigQueryClient(client=FakeClient(rows=[{"created_by": "a@x.com"}]))
        assert bq.created_by("ds", "trackers", "S1") == "a@x.com"

    def test_takes_the_earliest_claim(self):
        # Rows stream, so a fresh claim stays invisible here for minutes and a
        # second caller can write a claim of their own in that window. Ordering
        # ascending is what stops that second row stealing the tracker from the
        # person who created it -- do not flip this back to DESC.
        client = FakeClient(rows=[{"created_by": "first@x.com"}])
        BigQueryClient(client=client).created_by("ds", "trackers", "S1")
        sql = client.queries[0][0]
        assert "ORDER BY created_at ASC" in sql
        assert "DESC" not in sql

    def test_spreadsheet_id_is_a_bound_parameter(self):
        # Never interpolated into the SQL string.
        client = FakeClient(rows=[])
        BigQueryClient(client=client).created_by("ds", "trackers", "S'; DROP--")
        sql, job_config = client.queries[0]
        assert "DROP" not in sql
        assert job_config.query_parameters[0].value == "S'; DROP--"
