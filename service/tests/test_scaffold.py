"""Tests for scaffold and the BigQuery audit log, with fakes (no network)."""

from datetime import date

import pytest

from config import DEFAULT_CONFIG
from tracker import (
    ValidationError,
    build_tracker_record,
    create_named_ranges,
    date_to_serial,
    ensure_grid,
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
            v == [["Field", "Display name", "Type", "Formula", "Format",
                   "Show in views", "Break-out table", "Mapping"]]
            for _, v, _ in new.writes
        )

        existing = FakeSheet(
            {DEFAULT_CONFIG.setup_tab: 1, DEFAULT_CONFIG.data_source_tab: 2}
        )
        scaffold(existing, DEFAULT_CONFIG)
        assert existing.writes == []


class TestGenerateMapping:
    def _client(self):
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
        return FakeClient(setup, headers, serials, tabs)

    def test_dimension_columns_spill_live_unique_values(self):
        client = self._client()
        generate_mapping(client, DEFAULT_CONFIG)
        # A dimension column hardcodes only its header and the "**" sentinel;
        # the values under them are a live UNIQUE over the Data Source column,
        # so Mapping tracks new values without a redeploy.
        assert client._find_write(client.raw_writes, "A1") == [["Region"], ["**"]]
        assert client._find_write(client.formula_writes, "A3") == [[
            '=IFERROR(SORT(UNIQUE(FILTER('
            "'data_source'!B2:B, 'data_source'!B2:B<>\"\"))))"
        ]]

    def test_mapping_carries_the_available_dates(self):
        client = self._client()
        result = generate_mapping(client, DEFAULT_CONFIG)
        assert result["columns"] == 1  # Region
        assert result["has_dates"] is True

        # The date and Year columns sit after the dimension columns: header
        # row 1, then a live spill from row 2, no sentinel. INT collapses
        # datetimes to their day; YEAR to their year; descending sort puts
        # the newest on top.
        assert client._find_write(client.raw_writes, "B1") == [["Day"]]
        assert client._find_write(client.raw_writes, "C1") == [["Year"]]
        assert client._find_write(client.formula_writes, "B2") == [[
            "=IFERROR(SORT(UNIQUE(ARRAYFORMULA(INT("
            "FILTER('data_source'!A2:A, ISNUMBER('data_source'!A2:A))))), "
            "1, FALSE))"
        ]]
        assert client._find_write(client.formula_writes, "C2") == [[
            "=IFERROR(SORT(UNIQUE(ARRAYFORMULA(YEAR("
            "FILTER('data_source'!A2:A, ISNUMBER('data_source'!A2:A))))), "
            "1, FALSE))"
        ]]

        # The date column is formatted as dates, open-ended: the spill's
        # length changes with the data, so the format runs to the grid
        # bottom rather than a row count known at deploy time.
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
        assert "endRowIndex" not in date_fmt[0]["range"]


class FakeGrid:
    """Fake client exposing one tab's grid size, recording batch updates."""

    def __init__(self, rows, cols, title="Daily", sheet_id=7):
        self._props = {
            "title": title,
            "sheetId": sheet_id,
            "gridProperties": {"rowCount": rows, "columnCount": cols},
        }
        self.batch_requests = []

    def get_spreadsheet(self):
        return {"sheets": [{"properties": self._props}]}

    def batch_update(self, requests):
        self.batch_requests.append(requests)


def grid_of(client):
    props = client.batch_requests[0][0]["updateSheetProperties"]["properties"]
    return props["gridProperties"]


class TestEnsureGrid:
    def test_no_update_when_the_grid_already_fits(self):
        client = FakeGrid(1000, 26)
        ensure_grid(client, "Daily", 500, 20)
        assert client.batch_requests == []

    def test_no_update_when_the_grid_fits_exactly(self):
        client = FakeGrid(1000, 26)
        ensure_grid(client, "Daily", 1000, 26)
        assert client.batch_requests == []

    def test_grows_rows_past_the_default(self):
        client = FakeGrid(1000, 26)
        ensure_grid(client, "Daily", 1200, 26)
        assert grid_of(client) == {"rowCount": 1200, "columnCount": 26}

    def test_grows_columns_past_the_default(self):
        client = FakeGrid(1000, 26)
        ensure_grid(client, "Daily", 100, 60)
        assert grid_of(client) == {"rowCount": 1000, "columnCount": 60}

    def test_never_shrinks_the_other_dimension(self):
        # Growing rows must not narrow a tab that is already wider than asked.
        client = FakeGrid(1000, 80)
        ensure_grid(client, "Daily", 1200, 60)
        assert grid_of(client) == {"rowCount": 1200, "columnCount": 80}

    def test_targets_the_named_tab_by_its_sheet_id(self):
        client = FakeGrid(1000, 26, sheet_id=42)
        ensure_grid(client, "Daily", 1200, 26)
        props = client.batch_requests[0][0]["updateSheetProperties"]
        assert props["properties"]["sheetId"] == 42
        assert props["fields"] == "gridProperties.rowCount,gridProperties.columnCount"

    def test_matches_the_tab_case_insensitively(self):
        client = FakeGrid(1000, 26, title="DAILY")
        ensure_grid(client, "daily", 1200, 26)
        assert grid_of(client) == {"rowCount": 1200, "columnCount": 26}

    def test_unknown_tab_is_left_alone(self):
        client = FakeGrid(1000, 26)
        ensure_grid(client, "Nope", 5000, 90)
        assert client.batch_requests == []


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
ROW_COUNT = 152227


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

    def get_spreadsheet(self):
        return {
            "sheets": [
                {
                    "properties": {
                        "sheetId": DATA_SOURCE_SHEET_ID,
                        "gridProperties": {"rowCount": ROW_COUNT},
                    }
                }
            ]
        }

    def get_named_ranges(self):
        return self._named

    def batch_update(self, requests):
        self.batch_requests.extend(requests)


def _existing(name, col, **extra):
    """An existing named range as the API returns it, plus any overrides.

    Bounded at ROW_COUNT, which is how Sheets stores whatever we send.
    """
    grid = {
        "sheetId": DATA_SOURCE_SHEET_ID,
        "startRowIndex": 1,
        "startColumnIndex": col,
        "endColumnIndex": col + 1,
        "endRowIndex": ROW_COUNT,
    }
    grid.update(extra)
    return {"namedRangeId": "id_" + name, "name": name, "range": grid}


class TestCreateNamedRanges:
    def test_new_column_range_is_open_ended(self):
        client = FakeNamedRanges(["Spend"])
        result = create_named_ranges(client, DEFAULT_CONFIG)
        assert result["created"] == ["Spend"]
        grid = client.batch_requests[0]["addNamedRange"]["namedRange"]["range"]
        # We ask for 'data_source'!A2:A. Sheets stores it bounded at rowCount
        # regardless, but asking open-ended is what pins it to the CURRENT
        # height rather than a stale one.
        assert grid["startRowIndex"] == 1
        assert "endRowIndex" not in grid

    def test_range_bounded_at_the_grid_is_left_alone(self):
        # The convergence case: bounded at rowCount is as open as Sheets
        # allows, so it must not be rewritten on every run.
        client = FakeNamedRanges(["Spend"], {"Spend": _existing("Spend", 0)})
        result = create_named_ranges(client, DEFAULT_CONFIG)
        assert result == {"created": [], "updated": [], "skipped": ["Spend"]}
        assert client.batch_requests == []

    def test_range_bounded_short_of_the_grid_is_repaired(self):
        # The real defect: the grid grew and the bound did not follow, so rows
        # past it have dropped out of the SUMIFS.
        client = FakeNamedRanges(
            ["Spend"], {"Spend": _existing("Spend", 0, endRowIndex=1000)}
        )
        result = create_named_ranges(client, DEFAULT_CONFIG)
        assert result["updated"] == ["Spend"]
        update = client.batch_requests[0]["updateNamedRange"]
        assert update["fields"] == "range"
        assert update["namedRange"]["namedRangeId"] == "id_Spend"
        assert "endRowIndex" not in update["namedRange"]["range"]

    def test_unbounded_range_is_left_alone(self):
        # Ranges the Sheets UI created can be genuinely unbounded. Nothing to
        # repair — that is strictly better than what the API can write.
        stored = _existing("Spend", 0)
        del stored["range"]["endRowIndex"]
        client = FakeNamedRanges(["Spend"], {"Spend": stored})
        result = create_named_ranges(client, DEFAULT_CONFIG)
        assert result["skipped"] == ["Spend"]
        assert client.batch_requests == []

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


class TestNamedRangeCasing:
    """Sheets scopes named ranges case-insensitively: 'Year' and 'year' are
    one name. Matching existing ranges by exact spelling missed them, so a
    header whose capitalisation changed produced an addNamedRange for a name
    that already existed — a 400 that wedged every later run.
    """

    def test_existing_range_in_another_case_is_not_re_added(self):
        client = FakeNamedRanges(["Year"], {"year": _existing("year", 0)})
        result = create_named_ranges(client, DEFAULT_CONFIG)
        assert result["created"] == []
        adds = [r for r in client.batch_requests if "addNamedRange" in r]
        assert adds == []

    def test_existing_range_in_another_case_is_renamed(self):
        client = FakeNamedRanges(["Year"], {"year": _existing("year", 0)})
        result = create_named_ranges(client, DEFAULT_CONFIG)
        assert result["updated"] == ["Year"]
        update = client.batch_requests[0]["updateNamedRange"]
        assert update["namedRange"]["namedRangeId"] == "id_year"
        assert update["namedRange"]["name"] == "Year"
        # Only the spelling drifted, so the range is left out of the update.
        assert update["fields"] == "name"

    def test_casing_and_range_drift_are_repaired_together(self):
        client = FakeNamedRanges(
            ["Year"], {"year": _existing("year", 0, endRowIndex=1000)}
        )
        create_named_ranges(client, DEFAULT_CONFIG)
        update = client.batch_requests[0]["updateNamedRange"]
        assert update["fields"] == "range,name"
        assert "endRowIndex" not in update["namedRange"]["range"]
        assert update["namedRange"]["name"] == "Year"

    def test_matching_range_and_spelling_stays_a_no_op(self):
        client = FakeNamedRanges(["Year"], {"Year": _existing("Year", 0)})
        result = create_named_ranges(client, DEFAULT_CONFIG)
        assert result["skipped"] == ["Year"]
        assert client.batch_requests == []

    def test_two_headers_differing_only_in_case_yield_one_range(self):
        # Both sanitise into the same Sheets name, so the second must not be
        # added on top of the first.
        client = FakeNamedRanges(["Year", "year"])
        result = create_named_ranges(client, DEFAULT_CONFIG)
        assert result["created"] == ["Year"]
        assert len(client.batch_requests) == 1
