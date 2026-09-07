"""Integration tests for build_view / build_views with a fake sheets client."""

from datetime import date

import pytest

from config import DEFAULT_CONFIG
from tracker import (
    DEFAULT_BREAKOUT_CAP,
    ValidationError,
    build_comparison,
    build_view,
    build_views,
    date_to_serial,
)


# A Setup header row with the optional Display name column left out, and the
# full one. Most fixtures below use the former, so they also cover a tracker
# that simply never labels its fields.
SETUP_HEADER_NO_DISPLAY = [
    "Field", "Type", "Formula", "Format", "Show in views",
    "Break-out table", "Mapping",
]

SETUP_HEADER = [
    "Field", "Display name", "Type", "Formula", "Format", "Show in views",
    "Break-out table", "Mapping",
]


class FakeClient:
    """A fake covering the client surface build_view touches.

    Serves setup rows, data_source headers, the date column (as serials), and
    optionally Mapping rows; records batch writes and updates for assertions.
    """

    def __init__(self, setup_rows, headers, date_serials, tabs, mapping_rows=None,
                 setup_header=None):
        # Row 1 of Setup is the header row read_setup resolves columns from.
        self._setup = [setup_header or SETUP_HEADER_NO_DISPLAY] + list(setup_rows)
        self._headers = headers
        self._date_serials = date_serials
        self._tabs = dict(tabs)  # title -> sheetId
        self._mapping = mapping_rows or []
        # Sheets' default grid for a newly added tab.
        self.grid_rows = 1000
        self.grid_cols = 26
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
        # gridProperties mirror what addSheet really gives a new tab, so the
        # grid-growth path behaves here as it does against the API.
        return {
            "sheets": [
                {
                    "properties": {
                        "title": t,
                        "sheetId": sid,
                        "gridProperties": {
                            "rowCount": self.grid_rows,
                            "columnCount": self.grid_cols,
                        },
                    },
                    "charts": [],
                }
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

        # Header row one: the Year dropdown defaulting to yesterday's year.
        # (Header 3-4, KPI 6-7, compare 9-12, matrix 14-16+.)
        year = client._find_write(client.formula_writes, "A3")
        assert year == [["Year", "=YEAR(TODAY()-1)"]]

        # KPI header row: "Totals" + metric names.
        kpi = client._find_write(client.raw_writes, "A6")
        assert kpi == [["Totals", "Spend", "Clicks", "CPC"]]

        # KPI value row: raw metrics are SUMIFS; the calculated CPC simply
        # divides the sibling cells (which already respond to the slicers).
        grand = client._find_write(client.formula_writes, "B7")[0]
        assert grand[0].startswith("=SUMIFS(Spend")
        assert grand[2] == '=IFERROR(B7/C7, "")'

        # The period column anchors on 1 January of yesterday's year (the
        # Year dropdown scopes the break-outs, not the matrix) and steps
        # forward one month per row, blanking past the month containing
        # yesterday.
        periods = client._find_write(client.formula_writes, "A16")
        assert len(periods) == 12
        assert periods[0] == ["=DATE(YEAR(TODAY()-1),1,1)"]
        assert periods[1] == [
            '=IF(A16="","",IF(EDATE(A16,1)>TODAY()-1,"",EDATE(A16,1)))'
        ]

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

    def test_matrix_totals_row_sums_the_period_rows(self):
        client = _client(DEFAULT_CONFIG.monthly_tab)
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.monthly_tab, "month")
        # A Total row right under the 12 period rows (16-27): plain metrics
        # SUM their column; the calculated CPC divides the totals themselves.
        total = client._find_write(client.formula_writes, "A28")
        assert total == [[
            "Total",
            "=SUM(B16:B27)",
            "=SUM(C16:C27)",
            '=IFERROR(B28/C28, "")',
        ]]

    def test_chart_stops_above_the_totals_row(self):
        client = _client(DEFAULT_CONFIG.monthly_tab)
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.monthly_tab, "month")
        added = [r for batch in client.batch_updates for r in batch if "addChart" in r]
        domain = added[0]["addChart"]["chart"]["spec"]["basicChart"][
            "domains"][0]["domain"]["sourceRange"]["sources"][0]
        # Half-open end at index 27 = A1 row 27, the last period row; the
        # Total row (28) stays out of the trend line.
        assert domain["endRowIndex"] == 27

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

    def test_daily_rolling_window_and_date_dropdowns(self):
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
        # The period column is a plain rolling window, oldest first: the last
        # 14 days ending yesterday, ignoring the date dropdowns (they scope
        # the break-outs).
        periods = client._find_write(client.formula_writes, "A11")
        assert len(periods) == 14
        assert periods[0] == ["=TODAY()-14"]
        assert periods[1] == ["=A11+1"]
        # The window always fills, so metric cells are unguarded; the
        # calculated CPC references its row's sibling cells.
        matrix = client._find_write(client.formula_writes, "B11")
        assert matrix[0][0].startswith("=SUMIFS(Spend")
        assert matrix[0][2] == '=IFERROR(B11/C11, "")'
        assert matrix[1][2] == '=IFERROR(B12/C12, "")'

    def test_weekly_rolling_window_and_date_pickers(self):
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
        # Matrix data at A16: the last 6 Monday week-starts, oldest first,
        # ending on the week containing yesterday; pickers don't drive it.
        periods = client._find_write(client.formula_writes, "A16")
        assert len(periods) == 6
        assert periods[0] == ["=TODAY()-1-WEEKDAY(TODAY()-1,3)-35"]
        assert periods[1] == ["=A16+7"]
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
        # The legacy checkbox: TRUE reads as the default cap, so a tracker
        # built before the column held a number renders unchanged.
        client = _client(DEFAULT_CONFIG.weekly_tab, region_breakout="TRUE")
        result = build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.weekly_tab, "week")
        assert result["breakouts"] == ["Region"]
        # "By Region" break-out header: dimension name then the metrics.
        assert client._has_raw([["Region", "Spend", "Clicks", "CPC"]])
        # Its values come from the Mapping tab.
        assert client._has_raw([["North"], ["South"]])
        # And its label rows are swappable: a dropdown sourced below the
        # sentinel row covers them.
        assert any(
            "3:" in str(r["setDataValidation"]["rule"]["condition"]["values"])
            for batch in client.batch_updates
            for r in batch
            if "setDataValidation" in r
            and r["setDataValidation"].get("rule", {}).get(
                "condition", {}).get("type") == "ONE_OF_RANGE"
        )
        # A break-out cell pins the dimension to the row's value label and is
        # bounded by the tab's date pickers (blank picker = unbounded side).
        breakout = [
            w for w in client.formula_writes
            if w["values"] and "SUMIFS(Spend, Region, A" in str(w["values"][0][0])
        ]
        assert breakout
        cell = breakout[0]["values"][0][0]
        # The bounds are plain refs to the hidden window cells; the
        # blank-picker handling lives in those cells, not here.
        assert 'Day, ">="&$K$3' in cell
        assert 'Day, "<"&$L$3' in cell
        # The window cells themselves resolve the pickers, and their columns
        # are hidden from readers.
        assert client._find_write(client.formula_writes, "K3") == [
            ['=IF($B$3="",0,$B$3)', '=IF($D$3="",9.9E+307,$D$3+1)']
        ]
        hidden = [
            r["updateDimensionProperties"] for batch in client.batch_updates
            for r in batch if "updateDimensionProperties" in r
        ]
        assert any(
            h["range"]["startIndex"] == 10 and h["range"]["endIndex"] == 12
            and h["properties"] == {"hiddenByUser": True}
            for h in hidden
        )


    def test_breakout_totals_row_sums_the_visible_rows(self):
        client = _client(DEFAULT_CONFIG.weekly_tab, region_breakout="TRUE")
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.weekly_tab, "week")
        # The break-out's value rows sit at 27-28 (weekly matrix ends at its
        # Total row 22); the block's own Total row sums exactly those cells.
        total = client._find_write(client.formula_writes, "A29")
        assert total == [[
            "Total",
            "=SUM(B27:B28)",
            "=SUM(C27:C28)",
            '=IFERROR(B29/C29, "")',
        ]]


class TestBreakoutCap:
    """A break-out gets the rows its Setup cap asks for, labels swappable."""

    def _built(self, cap):
        client = _client(DEFAULT_CONFIG.weekly_tab, region_breakout=cap)
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.weekly_tab, "week")
        return client

    def _pickers(self, client):
        """The label-column swap dropdowns.

        They are the only ONE_OF_RANGE rules sourced from Mapping row 3 —
        slicers and date dropdowns all start at row 2 (the sentinel row).
        """
        rules = [
            r["setDataValidation"] for batch in client.batch_updates
            for r in batch
            if "setDataValidation" in r
            and r["setDataValidation"].get("rule", {}).get(
                "condition", {}).get("type") == "ONE_OF_RANGE"
        ]
        return [
            r for r in rules
            if "3:" in r["rule"]["condition"]["values"][0]["userEnteredValue"]
        ]

    def test_a_cap_below_the_value_count_truncates_and_says_so(self):
        client = self._built("1")
        assert client._has_raw([["By Region  (first 1 of 2)"]])
        # Only the first Mapping value is pre-filled.
        assert client._has_raw([["North"]])
        assert not client._has_raw([["North"], ["South"]])

    def test_a_cap_above_the_value_count_lists_everything(self):
        client = self._built("30")
        # No truncation, so no suffix on the title.
        assert client._has_raw([["By Region"]])
        assert client._has_raw([["North"], ["South"]])

    def test_the_label_column_gets_one_dropdown_covering_its_rows(self):
        pickers = self._pickers(self._built("30"))
        assert len(pickers) == 1
        rng = pickers[0]["range"]
        assert rng["startColumnIndex"] == 0
        assert rng["endColumnIndex"] == 1
        # Row 27 in A1 terms is index 26, and both pre-filled rows are
        # swappable — the dropdown covers exactly the block's value rows,
        # leaving the Total row below them fixed.
        assert rng["startRowIndex"] == 26
        assert rng["endRowIndex"] == 28

    def test_the_swap_list_skips_the_all_sentinel(self):
        picker = self._pickers(self._built("30"))[0]
        source = picker["rule"]["condition"]["values"][0]["userEnteredValue"]
        # Mapping row 2 is "**" (meaning "All"); as a row label it would total
        # every row rather than one value, so the list starts at row 3.
        assert source == "='mapping'!A3:A"

    def test_a_capped_totals_row_sums_only_the_visible_rows(self):
        client = self._built("1")
        # One visible row, so the Total covers that single cell — the column
        # always adds up visually, even when the title says rows were cut.
        total = client._find_write(client.formula_writes, "A28")
        assert total == [[
            "Total",
            "=SUM(B27:B27)",
            "=SUM(C27:C27)",
            '=IFERROR(B28/C28, "")',
        ]]

    def test_an_empty_breakout_gets_no_totals_row(self):
        # A broken-out dimension whose Mapping column has no values yet: the
        # block renders title + header only, so no Total row either. The one
        # "Total" write left is the period matrix's.
        setup = [
            ["Day", "date", "", "", "", ""],
            ["Region", "dimension", "", "", "TRUE", "TRUE"],
            ["Spend", "metric", "", "currency", "", ""],
        ]
        mapping = [["Region"], ["**"]]
        client = FakeClient(
            setup,
            ["Day", "Region", "Spend"],
            [date_to_serial(date(2025, 8, 4))],
            {"setup": 1, "data_source": 2, DEFAULT_CONFIG.weekly_tab: 3},
            mapping_rows=mapping,
        )
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.weekly_tab, "week")
        totals = [
            w for w in client.formula_writes
            if w["values"] and w["values"][0] and w["values"][0][0] == "Total"
        ]
        assert len(totals) == 1

    def test_capped_blocks_stack_in_setup_order(self):
        setup = [
            ["Day", "date", "", "", "", ""],
            ["Region", "dimension", "", "", "TRUE", "50"],
            ["Campaign", "dimension", "", "", "", "1"],
            ["Spend", "metric", "", "currency", "", ""],
        ]
        mapping = [
            ["Region", "Campaign"],
            ["**", "**"],
            ["North", "Alpha"],
            ["South", "Beta"],
        ]
        client = FakeClient(
            setup,
            ["Day", "Region", "Campaign", "Spend"],
            [date_to_serial(date(2025, 8, 4))],
            {"setup": 1, "data_source": 2, DEFAULT_CONFIG.weekly_tab: 3},
            mapping_rows=mapping,
        )
        result = build_view(
            client, DEFAULT_CONFIG, DEFAULT_CONFIG.weekly_tab, "week")
        assert result["breakouts"] == ["Region", "Campaign"]
        # Region's cap fits everything; Campaign's truncates at one row.
        assert client._has_raw([["By Region"]])
        assert client._has_raw([["North"], ["South"]])
        assert client._has_raw([["By Campaign  (first 1 of 2)"]])
        assert client._has_raw([["Alpha"]])
        assert not client._has_raw([["Alpha"], ["Beta"]])
        # Each block gets its own swap dropdown, sourced from its own Mapping
        # column, and Campaign's sits below Region's.
        pickers = self._pickers(client)
        sources = [
            p["rule"]["condition"]["values"][0]["userEnteredValue"]
            for p in pickers
        ]
        assert sources == ["='mapping'!A3:A", "='mapping'!B3:B"]
        region, campaign = pickers
        assert region["range"]["endRowIndex"] - region["range"]["startRowIndex"] == 2
        assert campaign["range"]["endRowIndex"] - campaign["range"]["startRowIndex"] == 1
        assert campaign["range"]["startRowIndex"] > region["range"]["endRowIndex"]


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


class TestGridFits:
    """A tab's grid has to cover every range the build writes into it.

    The Sheets API rejects an out-of-bounds range outright, so a tracker with
    enough broken-out dimensions to stack past the default 1000 rows used to
    fail the whole run with a bare "Sheets API error".
    """

    def _grid_updates(self, client):
        return [
            r["updateSheetProperties"]["properties"]["gridProperties"]
            for batch in client.batch_updates for r in batch
            if "updateSheetProperties" in r
            and "rowCount" in r["updateSheetProperties"]["properties"]
                .get("gridProperties", {})
        ]

    def _tall_client(self, n_breakouts):
        # Each broken-out dimension adds a table of up to its row cap (the
        # default here), so enough of them push the tab past 1000 rows.
        setup = [["Day", "date", "", "", "", ""]]
        dims = ["Dim{}".format(i) for i in range(n_breakouts)]
        for dim in dims:
            setup.append([dim, "dimension", "", "", "TRUE", "TRUE"])
        setup.append(["Spend", "metric", "", "currency", "", ""])
        mapping = [dims, ["**"] * n_breakouts]
        for i in range(DEFAULT_BREAKOUT_CAP):
            mapping.append(["v{}".format(i)] * n_breakouts)
        tabs = {"setup": 1, "data_source": 2, DEFAULT_CONFIG.daily_tab: 3}
        return FakeClient(setup, ["Day"] + dims + ["Spend"],
                          [date_to_serial(date(2025, 8, 4))], tabs,
                          mapping_rows=mapping)

    def test_short_tab_leaves_the_default_grid_alone(self):
        client = _client(DEFAULT_CONFIG.daily_tab, region_breakout="TRUE")
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.daily_tab, "day")
        assert self._grid_updates(client) == []

    def test_tall_tab_grows_the_grid_to_cover_its_rows(self):
        client = self._tall_client(25)
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.daily_tab, "day")
        updates = self._grid_updates(client)
        assert len(updates) == 1
        # Grown past the default, and only in the dimension that needed it.
        assert updates[0]["rowCount"] > 1000
        assert updates[0]["columnCount"] == 26

    def test_every_written_range_stays_inside_the_grown_grid(self):
        client = self._tall_client(25)
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.daily_tab, "day")
        updates = self._grid_updates(client)
        rows, cols = updates[0]["rowCount"], updates[0]["columnCount"]
        for batch in client.batch_updates:
            for req in batch:
                for payload in req.values():
                    rng = payload.get("range") if isinstance(payload, dict) else None
                    if not isinstance(rng, dict) or "sheetId" not in rng:
                        continue
                    assert rng.get("endRowIndex", 0) <= rows
                    assert rng.get("endColumnIndex", 0) <= cols

    def test_validation_clear_is_clamped_to_the_tab_width(self):
        # DV_CLEAR_COLS is wider than the default grid; sweeping that far would
        # be rejected, and there can be no rules past the last column anyway.
        client = _client(DEFAULT_CONFIG.daily_tab)
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.daily_tab, "day")
        clears = [
            r["setDataValidation"]["range"] for batch in client.batch_updates
            for r in batch
            if "setDataValidation" in r and "rule" not in r["setDataValidation"]
        ]
        assert len(clears) == 1
        assert clears[0]["endColumnIndex"] == 26


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


class TestDisplayNames:
    """Setup's Display name column relabels the dashboards, nothing else."""

    def _client(self, tab):
        setup = [
            ["Day", "", "date", "", "", "", "", ""],
            ["Region", "Market Region", "dimension", "", "", "TRUE", "TRUE", ""],
            ["Spend", "Media Spend", "metric", "", "currency", "", "", ""],
            ["Clicks", "", "metric", "", "number", "", "", ""],
            ["CPC", "Cost per Click", "calculated", "[Spend]/[Clicks]",
             "currency", "", "", ""],
        ]
        return FakeClient(
            setup,
            ["Day", "Region", "Spend", "Clicks"],
            [date_to_serial(date(2025, 8, 4))],
            {"setup": 1, "data_source": 2, tab: 3},
            mapping_rows=[["Region"], ["**"], ["North"], ["South"]],
            setup_header=SETUP_HEADER,
        )

    def test_view_blocks_render_the_display_names(self):
        client = self._client(DEFAULT_CONFIG.weekly_tab)
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.weekly_tab, "week")
        labels = ["Media Spend", "Clicks", "Cost per Click"]
        # A metric with no display name keeps its field name (Clicks).
        assert client._has_raw([["Totals"] + labels])
        assert client._has_raw([["Period"] + labels])
        assert client._has_raw([["From", "To"] + labels])
        # The slicer label and the break-out table's title and header row.
        assert client._has_raw([["Market Region", DEFAULT_CONFIG.sentinel]])
        assert client._has_raw([["By Market Region"]])
        assert client._has_raw([["Market Region"] + labels])

    def test_formulas_still_bind_to_the_field_names(self):
        # Relabelling must not re-point anything: the SUMIFS still reference
        # the Spend / Region named ranges, which come from the Field column.
        client = self._client(DEFAULT_CONFIG.weekly_tab)
        result = build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.weekly_tab, "week")
        assert result["metrics"] == ["Spend", "Clicks", "CPC"]
        assert result["dimensions"] == ["Region"]
        formulas = str(client.formula_writes)
        assert "SUMIFS(Spend, Region," in formulas
        assert "Media Spend" not in formulas
        assert "Market Region" not in formulas

    def test_breakout_values_still_come_from_the_mapping_column(self):
        # Mapping is keyed by field name, so a relabelled dimension must still
        # find its values.
        client = self._client(DEFAULT_CONFIG.weekly_tab)
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.weekly_tab, "week")
        assert client._has_raw([["North"], ["South"]])

    def test_comparison_picker_and_table_use_the_display_names(self):
        client = self._client(DEFAULT_CONFIG.comparison_tab)
        build_comparison(client, DEFAULT_CONFIG)
        assert client._has_raw([["Media Spend"], ["Clicks"], ["Cost per Click"]])
        assert client._has_raw([["Market Region", DEFAULT_CONFIG.sentinel]])
        # The picker cell defaults to the first metric's label...
        controls = [w["values"][0] for w in client.raw_writes
                    if w["values"] and w["values"][0][0] == "Metric to chart"]
        assert controls and controls[0][1] == "Media Spend"
        # ...its dropdown offers the labels...
        lists = [
            v["rule"]["condition"]["values"] for batch in client.batch_updates
            for r in batch if "setDataValidation" in r
            for v in [r["setDataValidation"]]
            # The tab-wide clear is a setDataValidation with no rule at all.
            if v.get("rule", {}).get("condition", {}).get("type") == "ONE_OF_LIST"
        ]
        metric_list = [
            [x["userEnteredValue"] for x in vals] for vals in lists
            if "Media Spend" in [x["userEnteredValue"] for x in vals]
        ]
        assert metric_list == [["Media Spend", "Clicks", "Cost per Click"]]
        # ...and the array its MATCH searches carries the same labels, so the
        # picked one selects the right metric's expression.
        helper = [
            w for w in client.formula_writes
            if any("CHOOSE(MATCH(" in str(cell) for row in w["values"] for cell in row)
        ]
        assert helper
        cell = [c for row in helper[0]["values"] for c in row
                if "CHOOSE(MATCH(" in str(c)][0]
        assert '{"Media Spend";"Clicks";"Cost per Click"}' in cell


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
