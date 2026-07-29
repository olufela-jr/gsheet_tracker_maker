"""Tests for scaffold and the BigQuery audit log, with fakes (no network)."""

from datetime import date

import pytest

from config import DEFAULT_CONFIG
from tracker import (
    ValidationError,
    build_tracker_record,
    create_named_ranges,
    date_to_serial,
    generate_mapping,
    log_tracker,
    require_input_tabs,
    scaffold,
)
from test_views import FakeClient


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
        assert any(
            v == [["Field", "Type", "Formula", "Format", "Show in views",
                   "Break-out table", "Mapping"]]
            for _, v, _ in new.writes
        )

        existing = FakeSheet(
            {DEFAULT_CONFIG.setup_tab: 1, DEFAULT_CONFIG.data_source_tab: 2}
        )
        scaffold(existing, DEFAULT_CONFIG)
        assert existing.writes == []


class TestGenerateMapping:
    def test_mapping_carries_the_available_dates(self):
        setup = [
            ["Day", "date", "", "", "", ""],
            ["Region", "dimension", "", "", "TRUE", ""],
            ["Spend", "metric", "", "currency", "", ""],
        ]
        headers = ["Day", "Region", "Spend"]
        serials = [
            date_to_serial(date(2025, 8, 5)),
            date_to_serial(date(2025, 8, 4)),
            date_to_serial(date(2025, 9, 1)),
            date_to_serial(date(2025, 8, 5)),  # duplicate day
        ]
        tabs = {"setup": 1, "data_source": 2, DEFAULT_CONFIG.mapping_tab: 3}
        client = FakeClient(setup, headers, serials, tabs)
        result = generate_mapping(client, DEFAULT_CONFIG)
        assert result["columns"] == 1  # Region
        assert result["dates"] == 3
        assert result["years"] == 1

        # The date column sits after the dimension columns: header row 1,
        # then the distinct day serials newest first, no sentinel.
        dates_col = client._find_write(client.raw_writes, "B1")
        assert dates_col == [
            ["Day"],
            [date_to_serial(date(2025, 9, 1))],
            [date_to_serial(date(2025, 8, 5))],
            [date_to_serial(date(2025, 8, 4))],
        ]

        # The years column follows: the distinct years of those dates.
        years_col = client._find_write(client.raw_writes, "C1")
        assert years_col == [["Year"], [2025]]

        # The serials are formatted as dates.
        fmts = [
            r["repeatCell"] for batch in client.batch_updates
            for r in batch if "repeatCell" in r
        ]
        date_fmt = [
            f for f in fmts
            if f["cell"]["userEnteredFormat"].get("numberFormat", {}).get("type") == "DATE"
            and f["range"]["startColumnIndex"] == 1
        ]
        assert len(date_fmt) == 1
        assert date_fmt[0]["range"]["endRowIndex"] == 4  # header + 3 dates


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


DATA_SOURCE_SHEET_ID = 7


class FakeNamedRanges:
    """Fake covering the client surface create_named_ranges touches."""

    def __init__(self, headers, named_ranges=None):
        self._headers = headers
        self._named = named_ranges or {}
        self.batch_requests = []

    def read_range(self, a1_range, unformatted=False):
        return [self._headers]

    def get_sheet_id(self, title):
        return DATA_SOURCE_SHEET_ID

    def get_named_ranges(self):
        return self._named

    def batch_update(self, requests):
        self.batch_requests.extend(requests)


def _existing(name, col, **extra):
    """An existing named range as the API returns it, plus any overrides."""
    grid = {
        "sheetId": DATA_SOURCE_SHEET_ID,
        "startRowIndex": 1,
        "startColumnIndex": col,
        "endColumnIndex": col + 1,
    }
    grid.update(extra)
    return {"namedRangeId": "id_" + name, "name": name, "range": grid}


class TestCreateNamedRanges:
    def test_new_column_range_is_open_ended(self):
        client = FakeNamedRanges(["Spend"])
        result = create_named_ranges(client, DEFAULT_CONFIG)
        assert result["created"] == ["Spend"]
        grid = client.batch_requests[0]["addNamedRange"]["namedRange"]["range"]
        # Row 2 down, with no bottom bound: 'data_source'!A2:A.
        assert grid["startRowIndex"] == 1
        assert "endRowIndex" not in grid

    def test_range_already_open_ended_is_left_alone(self):
        client = FakeNamedRanges(["Spend"], {"Spend": _existing("Spend", 0)})
        result = create_named_ranges(client, DEFAULT_CONFIG)
        assert result == {"created": [], "updated": [], "skipped": ["Spend"]}
        assert client.batch_requests == []

    def test_capped_range_is_repaired(self):
        # The bug: a range bounded at the sheet's row count stops feeding the
        # SUMIFS once the data grows past it. Re-running must rewrite it.
        client = FakeNamedRanges(
            ["Spend"], {"Spend": _existing("Spend", 0, endRowIndex=1000)}
        )
        result = create_named_ranges(client, DEFAULT_CONFIG)
        assert result["updated"] == ["Spend"]
        update = client.batch_requests[0]["updateNamedRange"]
        assert update["fields"] == "range"
        assert update["namedRange"]["namedRangeId"] == "id_Spend"
        assert "endRowIndex" not in update["namedRange"]["range"]

    def test_range_on_the_wrong_column_is_repointed(self):
        # Spend is column B here, but its range still points at column A.
        client = FakeNamedRanges(
            ["Clicks", "Spend"], {"Spend": _existing("Spend", 0)}
        )
        result = create_named_ranges(client, DEFAULT_CONFIG)
        assert result["created"] == ["Clicks"]
        assert result["updated"] == ["Spend"]
        update = [r for r in client.batch_requests if "updateNamedRange" in r][0]
        grid = update["updateNamedRange"]["namedRange"]["range"]
        assert grid["startColumnIndex"] == 1 and grid["endColumnIndex"] == 2

    def test_column_a_is_not_rewritten_every_run(self):
        # The API omits a zero start index, so column A must still compare equal.
        stored = _existing("Spend", 0)
        del stored["range"]["startColumnIndex"]
        client = FakeNamedRanges(["Spend"], {"Spend": stored})
        result = create_named_ranges(client, DEFAULT_CONFIG)
        assert result["skipped"] == ["Spend"]
        assert client.batch_requests == []

    def test_blank_headers_are_skipped(self):
        client = FakeNamedRanges(["Spend", "", None, "Clicks"])
        result = create_named_ranges(client, DEFAULT_CONFIG)
        assert result["created"] == ["Spend", "Clicks"]

    def test_headers_sanitising_to_one_name_yield_one_range(self):
        client = FakeNamedRanges(["Ad Spend", "Ad/Spend"])
        result = create_named_ranges(client, DEFAULT_CONFIG)
        assert result["created"] == ["Ad_Spend"]
        assert len(client.batch_requests) == 1

    def test_missing_data_source_tab_raises(self):
        client = FakeNamedRanges(["Spend"])
        client.get_sheet_id = lambda title: None
        with pytest.raises(ValueError):
            create_named_ranges(client, DEFAULT_CONFIG)
