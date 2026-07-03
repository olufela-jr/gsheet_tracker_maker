"""Tests for scaffold and the BigQuery audit log, with fakes (no network)."""

import pytest

from config import DEFAULT_CONFIG
from tracker import (
    ValidationError,
    build_tracker_record,
    log_tracker,
    require_input_tabs,
    scaffold,
)


class FakeSheet:
    """Fake client for a single spreadsheet's metadata and writes."""

    def __init__(self, sheet_titles):
        # sheet_titles maps title -> sheetId
        self._sheets = sheet_titles
        self.spreadsheet_id = "CHILD_ID"
        self.batch_requests = []
        self.writes = []

    def get_spreadsheet(self):
        return {
            "sheets": [
                {"properties": {"title": t, "sheetId": sid}}
                for t, sid in self._sheets.items()
            ]
        }

    def batch_update(self, requests):
        self.batch_requests = requests

    def write_values(self, a1_range, values, value_input_option="RAW"):
        self.writes.append((a1_range, values, value_input_option))


class TestScaffold:
    def test_brand_new_sheet_renames_default_and_adds_data_source(self):
        # One default sheet: rename it to setup, add data_source.
        client = FakeSheet({"Sheet1": 0})
        scaffold(client, DEFAULT_CONFIG)
        kinds = [list(r.keys())[0] for r in client.batch_requests]
        assert kinds.count("updateSheetProperties") == 1
        assert kinds.count("addSheet") == 1
        rename = client.batch_requests[0]["updateSheetProperties"]
        assert rename["properties"]["title"] == DEFAULT_CONFIG.setup_tab

    def test_no_changes_when_input_tabs_present(self):
        client = FakeSheet(
            {DEFAULT_CONFIG.setup_tab: 1, DEFAULT_CONFIG.data_source_tab: 2}
        )
        scaffold(client, DEFAULT_CONFIG)
        assert client.batch_requests == []

    def test_input_tabs_matched_case_insensitively(self):
        # User named them in a different case; we must not try to recreate them.
        client = FakeSheet({"SETUP": 1, "Data_Source": 2})
        scaffold(client, DEFAULT_CONFIG)
        assert client.batch_requests == []

    def test_only_creates_the_missing_input_tab(self):
        # setup exists, data_source missing: add data_source only, no rename.
        client = FakeSheet({DEFAULT_CONFIG.setup_tab: 1, "Notes": 9})
        scaffold(client, DEFAULT_CONFIG)
        kinds = [list(r.keys())[0] for r in client.batch_requests]
        assert kinds.count("updateSheetProperties") == 0
        assert kinds.count("addSheet") == 1
        added = client.batch_requests[0]["addSheet"]["properties"]["title"]
        assert added == DEFAULT_CONFIG.data_source_tab

    def test_never_deletes(self):
        client = FakeSheet({"Sheet1": 0, "Notes": 9})
        scaffold(client, DEFAULT_CONFIG)
        kinds = [list(r.keys())[0] for r in client.batch_requests]
        assert "deleteSheet" not in kinds

    def test_seeds_setup_header_only_when_setup_created(self):
        new = FakeSheet({"Sheet1": 0})
        scaffold(new, DEFAULT_CONFIG)
        assert any(v == [["Field", "Type"]] for _, v, _ in new.writes)

        existing = FakeSheet(
            {DEFAULT_CONFIG.setup_tab: 1, DEFAULT_CONFIG.data_source_tab: 2}
        )
        scaffold(existing, DEFAULT_CONFIG)
        assert existing.writes == []


class TestRequireInputTabs:
    def test_passes_when_both_present(self):
        client = FakeSheet(
            {DEFAULT_CONFIG.setup_tab: 1, DEFAULT_CONFIG.data_source_tab: 2}
        )
        require_input_tabs(client, DEFAULT_CONFIG)  # no raise

    def test_matches_case_insensitively(self):
        client = FakeSheet({"SETUP": 1, "Data_Source": 2})
        require_input_tabs(client, DEFAULT_CONFIG)  # no raise

    def test_raises_naming_the_missing_tab(self):
        client = FakeSheet({DEFAULT_CONFIG.setup_tab: 1})  # no data_source
        with pytest.raises(ValidationError) as exc:
            require_input_tabs(client, DEFAULT_CONFIG)
        assert any("data_source" in e for e in exc.value.errors)

    def test_raises_when_neither_present(self):
        client = FakeSheet({"Sheet1": 0})
        with pytest.raises(ValidationError):
            require_input_tabs(client, DEFAULT_CONFIG)


class TestBuildTrackerRecord:
    def test_all_fields_in_schema_order(self):
        record = build_tracker_record(
            event_id="evt-1",
            created_at="2026-06-12T10:00:00+00:00",
            spreadsheet_id="CHILD_ID",
            url="https://docs.google.com/spreadsheets/d/CHILD_ID/edit",
            title="Q3 Sales",
            client="Acme",
            sub_brand="Acme Fizz",
            created_by="alice@yourco.com",
            status="active",
            service_revision="tracker-service-00001-abc",
        )
        assert record == {
            "event_id": "evt-1",
            "created_at": "2026-06-12T10:00:00+00:00",
            "spreadsheet_id": "CHILD_ID",
            "url": "https://docs.google.com/spreadsheets/d/CHILD_ID/edit",
            "title": "Q3 Sales",
            "client": "Acme",
            "sub_brand": "Acme Fizz",
            "created_by": "alice@yourco.com",
            "status": "active",
            "service_revision": "tracker-service-00001-abc",
        }

    def test_status_defaults_to_active(self):
        record = build_tracker_record(
            event_id="e", created_at="t", spreadsheet_id="X", url="u",
            title="ti", client="c", sub_brand="s", created_by="b",
        )
        assert record["status"] == "active"
        assert record["service_revision"] == ""

    def test_missing_values_become_blank(self):
        record = build_tracker_record(
            event_id="e", created_at="t", spreadsheet_id="X", url=None,
            title=None, client="Acme", sub_brand=None, created_by=None,
        )
        assert record["url"] == ""
        assert record["sub_brand"] == ""
        assert record["client"] == "Acme"


class FakeBigQuery:
    def __init__(self):
        self.inserted = []

    def insert_row(self, dataset, table, row):
        self.inserted.append((dataset, table, row))


class TestLogTracker:
    def test_inserts_record_into_configured_table(self):
        bq = FakeBigQuery()
        record = {"spreadsheet_id": "X", "client": "Acme"}
        returned = log_tracker(bq, DEFAULT_CONFIG, record)
        assert returned == record
        dataset, table, row = bq.inserted[0]
        assert dataset == DEFAULT_CONFIG.bigquery_dataset
        assert table == DEFAULT_CONFIG.bigquery_table
        assert row == record
