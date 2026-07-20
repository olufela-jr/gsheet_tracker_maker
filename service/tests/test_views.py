"""Integration tests for build_view / build_views with a fake sheets client."""

from datetime import date

import pytest

from config import DEFAULT_CONFIG
from tracker import (
    ValidationError,
    build_comparison,
    build_view,
    build_views,
    date_to_serial,
)


class FakeClient:
    """A fake covering the client surface build_view touches.

    Serves setup rows, data_source headers, the date column (as serials), and
    optionally Mapping rows; records batch writes and updates for assertions.
    """

    def __init__(self, setup_rows, headers, date_serials, tabs, mapping_rows=None):
        self._setup = setup_rows
        self._headers = headers
        self._date_serials = date_serials
        self._tabs = dict(tabs)  # title -> sheetId
        self._mapping = mapping_rows or []
        self.spreadsheet_id = "SHEET"
        self.raw_writes = []
        self.formula_writes = []
        self.batch_updates = []
        self.cleared = []
        self.reads = []

    def read_range(self, a1_range, unformatted=False):
        self.reads.append(a1_range)
        low = a1_range.lower()
        if "setup" in low:
            return self._setup
        if "mapping" in low:
            return self._mapping
        if "data_source" in low and "1:1" in a1_range:
            return [self._headers]
        if "data_source" in low:
            # the date column read (unformatted serials)
            return [[s] for s in self._date_serials]
        return []

    def get_spreadsheet(self):
        return {
            "sheets": [
                {"properties": {"title": t, "sheetId": sid}, "charts": []}
                for t, sid in self._tabs.items()
            ]
        }

    def get_sheet_id(self, title):
        for t, sid in self._tabs.items():
            if t.lower() == title.lower():
                return sid
        return None

    def clear_range(self, a1_range):
        self.cleared.append(a1_range)

    def batch_write_values(self, data, value_input_option="RAW"):
        if value_input_option == "RAW":
            self.raw_writes.extend(data)
        else:
            self.formula_writes.extend(data)

    def batch_update(self, requests):
        self.batch_updates.append(requests)

    def _find_write(self, writes, suffix):
        for w in writes:
            if w["range"].endswith(suffix):
                return w["values"]
        return None

    def _has_raw(self, value):
        return any(w["values"] == value for w in self.raw_writes)


def _client(granularity_tab, region_breakout=""):
    setup = [
        ["Day", "date", "", "", "", ""],
        ["Region", "dimension", "", "", "TRUE", region_breakout],
        ["Spend", "metric", "", "currency", "", ""],
        ["Clicks", "metric", "", "number", "", ""],
        ["CPC", "calculated", "[Spend]/[Clicks]", "currency", "", ""],
    ]
    headers = ["Day", "Region", "Spend", "Clicks"]
    serials = [
        date_to_serial(date(2025, 8, 4)),
        date_to_serial(date(2025, 8, 5)),
        date_to_serial(date(2025, 9, 1)),
    ]
    tabs = {"setup": 1, "data_source": 2, granularity_tab: 3}
    mapping = [["Region"], ["**"], ["North"], ["South"]]
    return FakeClient(setup, headers, serials, tabs, mapping_rows=mapping)


class TestBuildView:
    def test_monthly_window_kpi_and_matrix(self):
        client = _client(DEFAULT_CONFIG.monthly_tab)
        result = build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.monthly_tab, "month")
        # Monthly is one calendar year: 12 rows, January downwards.
        assert result["periods"] == 12
        assert result["metrics"] == ["Spend", "Clicks", "CPC"]

        # Header row one: the Year dropdown defaulting to the current year.
        # (Header 3-4, KPI 6-7, compare 9-12, matrix 14-16+.)
        year = client._find_write(client.formula_writes, "A3")
        assert year == [["Year", "=YEAR(TODAY())"]]

        # KPI header row: "Totals" + metric names.
        kpi = client._find_write(client.raw_writes, "A6")
        assert kpi == [["Totals", "Spend", "Clicks", "CPC"]]

        # KPI value row: raw metrics are SUMIFS; the calculated CPC simply
        # divides the sibling cells (which already respond to the slicers).
        grand = client._find_write(client.formula_writes, "B7")[0]
        assert grand[0].startswith("=SUMIFS(Spend")
        assert grand[2] == '=IFERROR(B7/C7, "")'

        # The period column anchors on 1 January of the picked year and steps
        # forward one month per row, going blank once past the current month.
        periods = client._find_write(client.formula_writes, "A16")
        assert len(periods) == 12
        assert periods[0] == ["=DATE($B$3,1,1)"]
        assert periods[1] == ['=IF(A16="","",IF(EDATE(A16,1)>TODAY(),"",EDATE(A16,1)))']

        # Main matrix: one column per metric (no change % columns); monthly
        # bounds use EOMONTH and each cell is blanked while its period cell
        # is (future months).
        header = client._find_write(client.raw_writes, "A15")
        assert header == [["Period", "Spend", "Clicks", "CPC"]]
        matrix = client._find_write(client.formula_writes, "B16")
        assert len(matrix) == 12  # one row per window period
        assert len(matrix[0]) == 3  # one column per metric
        assert matrix[0][0].startswith('=IF(A16="","",SUMIFS(Spend')
        assert "EOMONTH(A16,0)" in matrix[0][0]

    def test_monthly_year_dropdown_sources_available_years(self):
        client = _client(DEFAULT_CONFIG.monthly_tab)
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.monthly_tab, "month")
        # The Year cell (B3) is a dropdown of the Mapping years column (the
        # column after the dates column, so 'mapping'!C with one dimension).
        dvs = [
            r["setDataValidation"] for batch in client.batch_updates
            for r in batch if "setDataValidation" in r
        ]
        year_dds = [
            dv for dv in dvs
            if dv.get("rule", {}).get("condition", {}).get("type") == "ONE_OF_RANGE"
            and "'mapping'!C2:C" in str(dv["rule"]["condition"]["values"])
        ]
        assert len(year_dds) == 1
        assert year_dds[0]["range"]["startRowIndex"] == 2
        assert year_dds[0]["range"]["startColumnIndex"] == 1

    def test_monthly_compare_block_below_the_totals(self):
        client = _client(DEFAULT_CONFIG.monthly_tab)
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.monthly_tab, "month")
        # The comparison block sits below the KPI Totals (rows 6-7), with no
        # label column: just From | To | metrics.
        header = client._find_write(client.raw_writes, "A9")
        assert header == [["From", "To", "Spend", "Clicks", "CPC"]]
        rows = client._find_write(client.formula_writes, "A10")
        # The date cells start blank (no defaults); "% change" labels the
        # bottom row in the From column.
        assert rows[0][:2] == ["", ""]
        assert rows[1][:2] == ["", ""]
        assert rows[2][:2] == ["% change", ""]
        # Totals are date-ranged SUMIFS filtered by the slicer cell (B4)
        # above, blank until both dates of the row are picked.
        spend_a = rows[0][2]
        assert spend_a.startswith('=IF(OR($A10="",$B10=""),"",SUMIFS(Spend')
        assert '">="&$A10' in spend_a and '"<"&($B10+1)' in spend_a
        assert "IF(B4=" in spend_a
        # The calculated CPC column divides its row's sibling cells, inside
        # the same both-dates-picked guard.
        assert rows[0][4] == (
            '=IF(OR($A10="",$B10=""),"",IFERROR(C10/D10, ""))'
        )
        # % change per metric underneath, comparing the two rows.
        assert rows[2][2:] == [
            '=IFERROR((C11-C10)/C10, "")',
            '=IFERROR((D11-D10)/D10, "")',
            '=IFERROR((E11-E10)/E10, "")',
        ]
        # The From/To cells are dropdowns of the Mapping date column (the
        # column after the one mapped dimension, so 'mapping'!B).
        dvs = [
            r["setDataValidation"] for batch in client.batch_updates
            for r in batch if "setDataValidation" in r
        ]
        date_dds = [
            dv for dv in dvs
            if dv.get("rule", {}).get("condition", {}).get("type") == "ONE_OF_RANGE"
            and "'mapping'!B2:B" in str(dv["rule"]["condition"]["values"])
        ]
        assert len(date_dds) == 4  # From + To on both compare rows

    def test_monthly_adds_a_chart(self):
        client = _client(DEFAULT_CONFIG.monthly_tab)
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.monthly_tab, "month")
        added = [r for batch in client.batch_updates for r in batch if "addChart" in r]
        assert len(added) == 1

    def test_daily_no_chart_no_compare_and_day_bounds(self):
        client = _client(DEFAULT_CONFIG.daily_tab)
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.daily_tab, "day")
        added = [r for batch in client.batch_updates for r in batch if "addChart" in r]
        assert added == []
        # No compare block on daily.
        assert not client._has_raw([["Period A"], ["Period B"], ["% change"]])
        # Main table starts higher (no compare block) and has no delta columns.
        header = client._find_write(client.raw_writes, "A10")[0]
        assert header == ["Period", "Spend", "Clicks", "CPC"]
        matrix = client._find_write(client.formula_writes, "B11")
        assert "(A11+1)" in matrix[0][0]

    def test_daily_window_follows_the_date_dropdowns(self):
        client = _client(DEFAULT_CONFIG.daily_tab)
        result = build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.daily_tab, "day")
        assert result["periods"] == 14
        # The header's first row: Date from / Date to with NO defaults — the
        # cells are blank dropdowns of the available dates.
        header = client._find_write(client.raw_writes, "A3")
        assert header == [["Date from", "", "Date to", ""]]
        dvs = [
            r["setDataValidation"] for batch in client.batch_updates
            for r in batch if "setDataValidation" in r
        ]
        date_dds = [
            dv for dv in dvs
            if dv.get("rule", {}).get("condition", {}).get("type") == "ONE_OF_RANGE"
            and "'mapping'!B2:B" in str(dv["rule"]["condition"]["values"])
            and dv["range"]["startRowIndex"] == 2
        ]
        assert len(date_dds) == 2  # Date from + Date to
        # The period column runs newest first from the picked end date —
        # falling back to the newest available date — and blanks out before
        # the picked start (or 14 days below the effective end).
        periods = client._find_write(client.formula_writes, "A11")
        assert len(periods) == 14
        assert periods[0] == ["=IF($D$3=\"\",MAX('mapping'!B2:B),$D$3)"]
        assert periods[1] == [
            '=IF(A11="","",IF(A11-1<IF($B$3="",$A$11-13,$B$3),"",A11-1))'
        ]
        # Metric cells blank alongside their period cell; the calculated CPC
        # references its row's sibling cells inside the same guard.
        matrix = client._find_write(client.formula_writes, "B11")
        assert matrix[0][0].startswith('=IF(A11="","",SUMIFS(Spend')
        assert matrix[0][2] == '=IF(A11="","",IFERROR(B11/C11, ""))'
        assert matrix[1][2] == '=IF(A12="","",IFERROR(B12/C12, ""))'

    def test_weekly_window_follows_the_date_pickers(self):
        client = _client(DEFAULT_CONFIG.weekly_tab)
        result = build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.weekly_tab, "week")
        assert result["periods"] == 6
        # Header's date controls default to the last 4 weeks, ending yesterday.
        defaults = client._find_write(client.formula_writes, "A3")
        assert defaults == [["Date from", "=TODAY()-28", "Date to", "=TODAY()-1"]]
        # Compare block date cells start blank (dropdowns, no defaults).
        rows = client._find_write(client.formula_writes, "A10")
        assert rows[0][:2] == ["", ""]
        assert rows[1][:2] == ["", ""]
        # Matrix data at A16: Monday week-starts, newest first, blanking
        # before the week containing the picked start date.
        periods = client._find_write(client.formula_writes, "A16")
        assert len(periods) == 6
        assert periods[0] == ["=$D$3-WEEKDAY($D$3,3)"]
        assert periods[1] == [
            '=IF(A16="","",IF(A16-7<$B$3-WEEKDAY($B$3,3),"",A16-7))'
        ]
        # One column per metric, no delta columns.
        matrix = client._find_write(client.formula_writes, "B16")
        assert len(matrix[0]) == 3

    def test_header_pairs_seeded_with_sentinel(self):
        client = _client(DEFAULT_CONFIG.weekly_tab)
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.weekly_tab, "week")
        # One dimension: a single name | dropdown pair on the grid row, just
        # below the header's date-controls row (so row 4).
        pair = client._find_write(client.raw_writes, "A4")
        assert pair == [["Region", DEFAULT_CONFIG.sentinel]]
        # The Mapping dropdown is wired to the pair's value cell (B4).
        dvs = [
            r["setDataValidation"] for batch in client.batch_updates
            for r in batch if "setDataValidation" in r
        ]
        wired = [
            dv for dv in dvs
            if dv.get("rule", {}).get("condition", {}).get("type") == "ONE_OF_RANGE"
            and dv["range"]["startRowIndex"] == 3
            and dv["range"]["startColumnIndex"] == 1
        ]
        assert len(wired) == 1

    def test_header_stat_cells(self):
        client = _client(DEFAULT_CONFIG.daily_tab)
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.daily_tab, "day")
        stats = client._find_write(client.formula_writes, "I3")
        assert stats == [
            ["Today", "=TODAY()"],
            ['="Days Left in "&TEXT(TODAY(),"mmmm")',
             "=EOMONTH(TODAY(),0)-TODAY()+1"],
        ]

    def test_header_grid_wraps_after_four_pairs(self):
        setup = [
            ["Day", "date", "", "", "", ""],
            ["Region", "dimension", "", "", "TRUE", ""],
            ["Market", "dimension", "", "", "TRUE", ""],
            ["Channel", "dimension", "", "", "TRUE", ""],
            ["OS", "dimension", "", "", "TRUE", ""],
            ["Language", "dimension", "", "", "TRUE", ""],
            ["Spend", "metric", "", "currency", "", ""],
        ]
        headers = ["Day", "Region", "Market", "Channel", "OS", "Language", "Spend"]
        serials = [date_to_serial(date(2025, 8, 4))]
        tabs = {"setup": 1, "data_source": 2, DEFAULT_CONFIG.weekly_tab: 3}
        client = FakeClient(setup, headers, serials, tabs)
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.weekly_tab, "week")
        s = DEFAULT_CONFIG.sentinel
        # The slicer grid wraps below the header's date-controls row.
        row1 = client._find_write(client.raw_writes, "A4")
        assert row1 == [["Region", s, "Market", s, "Channel", s, "OS", s]]
        row2 = client._find_write(client.raw_writes, "A5")
        assert row2 == [["Language", s]]
        # A three-row header pushes the KPI strip (now directly below the
        # header) down one row: Totals at 7.
        assert client._find_write(client.raw_writes, "A7") == [["Totals", "Spend"]]

    def test_breakout_table_rendered(self):
        client = _client(DEFAULT_CONFIG.weekly_tab, region_breakout="TRUE")
        result = build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.weekly_tab, "week")
        assert result["breakouts"] == ["Region"]
        # "By Region" break-out header: dimension name then the metrics.
        assert client._has_raw([["Region", "Spend", "Clicks", "CPC"]])
        # Its values come from the Mapping tab.
        assert client._has_raw([["North"], ["South"]])
        # A break-out cell pins the dimension to the row's value label.
        breakout = [
            w for w in client.formula_writes
            if w["values"] and "SUMIFS(Spend, Region, A" in str(w["values"][0][0])
        ]
        assert breakout


class TestBuildViews:
    def _client_all_tabs(self):
        c = _client(DEFAULT_CONFIG.daily_tab)
        c._tabs[DEFAULT_CONFIG.weekly_tab] = 4
        c._tabs[DEFAULT_CONFIG.monthly_tab] = 5
        c._tabs[DEFAULT_CONFIG.comparison_tab] = 6
        return c

    def test_builds_three_views_and_comparison(self):
        client = self._client_all_tabs()
        results = build_views(client, DEFAULT_CONFIG)
        assert [r["granularity"] for r in results[:3]] == ["day", "week", "month"]
        assert results[-1]["tab"] == DEFAULT_CONFIG.comparison_tab

    def test_reads_setup_and_date_column_once(self):
        # The quota fix: the tabs must not re-read setup / headers / the date
        # column per tab. Expect one read each: setup, headers, date column.
        client = self._client_all_tabs()
        build_views(client, DEFAULT_CONFIG)
        setup_reads = [r for r in client.reads if "setup" in r.lower()]
        header_reads = [r for r in client.reads if "1:1" in r]
        assert len(setup_reads) == 1
        assert len(header_reads) == 1


class TestComparison:
    def _client(self):
        c = _client(DEFAULT_CONFIG.comparison_tab)
        return c

    def test_two_sides_dates_and_readout(self):
        client = self._client()
        result = build_comparison(client, DEFAULT_CONFIG)
        assert result["tab"] == DEFAULT_CONFIG.comparison_tab
        assert result["metrics"] == ["Spend", "Clicks", "CPC"]
        # Split-screen headers.
        assert client._has_raw([["SIDE A"]]) and client._has_raw([["SIDE B"]])
        # Each side has a Region dropdown and its own date range.
        assert any(w["values"] == [["Region", "**"]] for w in client.raw_writes)
        assert any(w["values"][0][0] == "Date from" for w in client.raw_writes)
        # Comparison table header.
        assert client._has_raw([["Metric", "Side A", "Side B", "% diff"]])

    def test_side_totals_use_date_range_and_dropdowns(self):
        client = self._client()
        build_comparison(client, DEFAULT_CONFIG)
        # The metrics table's Side A / Side B / %diff formulas.
        rows = None
        for w in client.formula_writes:
            v = w["values"]
            if v and isinstance(v[0][0], str) and v[0][0].startswith("=SUMIFS(Spend"):
                rows = v
                break
        assert rows is not None
        spend_a = rows[0][0]
        # Bounded by the side's from/to cells and filtered by the Region dropdown.
        assert '">="&B' in spend_a and '"<"&(B' in spend_a
        assert "Region, IF(" in spend_a
        assert rows[0][2].startswith("=IFERROR((C")  # % diff
        # The calculated CPC row references the sibling metric rows per side
        # (metrics render as rows on this tab).
        assert rows[2][0].startswith("=IFERROR(B")
        assert "/B" in rows[2][0] and "SUMIFS" not in rows[2][0]

    def test_trend_helper_and_chart(self):
        client = self._client()
        build_comparison(client, DEFAULT_CONFIG)
        # A CHOOSE/MATCH picks the charted metric per side.
        helper = [
            w for w in client.formula_writes
            if any("CHOOSE(MATCH(" in str(cell) for row in w["values"] for cell in row)
        ]
        assert helper
        # A trend line chart is added.
        added = [r for batch in client.batch_updates for r in batch if "addChart" in r]
        assert len(added) == 1
        chart = added[0]["addChart"]["chart"]["spec"]["basicChart"]
        assert len(chart["series"]) == 2  # Side A and Side B


class TestUnvalidatedFormula:
    """build_views is a standalone action, so bad formulas reach the builders."""

    def _client(self, formula):
        setup = [
            ["Day", "date", "", "", "", ""],
            ["Region", "dimension", "", "", "TRUE", ""],
            ["Spend", "metric", "", "currency", "", ""],
            ["Clicks", "metric", "", "number", "", ""],
            ["CPC", "calculated", formula, "currency", "", ""],
        ]
        return FakeClient(
            setup,
            ["Day", "Region", "Spend", "Clicks"],
            [date_to_serial(date(2025, 8, 4))],
            {"setup": 1, "data_source": 2, DEFAULT_CONFIG.monthly_tab: 3},
            mapping_rows=[["Region"], ["**"], ["North"]],
        )

    def test_wrong_case_token_raises_validation_error_not_keyerror(self):
        client = self._client("[Spend]/[clicks]")
        with pytest.raises(ValidationError) as exc:
            build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.monthly_tab, "month")
        assert any("Did you mean [Clicks]?" in e for e in exc.value.errors)

    def test_unknown_token_raises_validation_error(self):
        client = self._client("[Spend]/[Nope]")
        with pytest.raises(ValidationError) as exc:
            build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.monthly_tab, "month")
        assert any("unknown field 'Nope'" in e for e in exc.value.errors)
