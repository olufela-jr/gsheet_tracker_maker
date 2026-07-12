"""Integration tests for build_view / build_views with a fake sheets client."""

from datetime import date

from config import DEFAULT_CONFIG
from tracker import build_comparison, build_view, build_views, date_to_serial


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
        ["CPC", "metric", "[Spend]/[Clicks]", "currency", "", ""],
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
    def test_monthly_buckets_and_kpi_and_matrix(self):
        client = _client(DEFAULT_CONFIG.monthly_tab)
        result = build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.monthly_tab, "month")
        # Aug and Sep -> two monthly buckets.
        assert result["buckets"] == 2
        assert result["metrics"] == ["Spend", "Clicks", "CPC"]

        # KPI header row: "Totals" + metric names.
        kpi = client._find_write(client.raw_writes, "A6")
        assert kpi == [["Totals", "Spend", "Clicks", "CPC"]]

        # KPI value row: grand totals (calc uses IFERROR + division).
        grand = client._find_write(client.formula_writes, "B7")[0]
        assert grand[0].startswith("=SUMIFS(Spend")
        assert grand[2].startswith('=IFERROR(SUMIFS(Spend')
        assert "/SUMIFS(Clicks" in grand[2]

        # Main matrix sits below the compare block; monthly bounds use EOMONTH.
        matrix = client._find_write(client.formula_writes, "B18")
        assert len(matrix) == 2  # one row per bucket
        assert "EOMONTH(A18,0)" in matrix[0][0]
        assert matrix[1][0].startswith("=SUMIFS(Spend")

    def test_monthly_has_compare_block_and_delta_columns(self):
        client = _client(DEFAULT_CONFIG.monthly_tab)
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.monthly_tab, "month")
        # Compare header row.
        assert client._has_raw([["Metric", "Period A", "Period B", "Change"]])
        # Compare change formula for the first metric.
        cmp = client._find_write(client.formula_writes, "B11")
        assert 'IFERROR((C11-B11)/B11' in cmp[0][2]
        # Main header carries a change column beside each metric.
        header = client._find_write(client.raw_writes, "A17")[0]
        assert header.count("change %") == 3
        # Delta cell references the value cell above it.
        matrix = client._find_write(client.formula_writes, "B18")
        assert matrix[0][1] == ""  # first bucket has no previous
        assert "IFERROR((B19-B18)/B18" in matrix[1][1]

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
        assert not client._has_raw([["Metric", "Period A", "Period B", "Change"]])
        # Main table starts higher (no compare block) and has no delta columns.
        header = client._find_write(client.raw_writes, "A10")[0]
        assert header == ["Period", "Spend", "Clicks", "CPC"]
        matrix = client._find_write(client.formula_writes, "B11")
        assert "(A11+1)" in matrix[0][0]

    def test_dropdown_seeded_with_sentinel(self):
        client = _client(DEFAULT_CONFIG.weekly_tab)
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.weekly_tab, "week")
        drop = client._find_write(client.raw_writes, "A4")
        assert drop == [[DEFAULT_CONFIG.sentinel]]  # one dimension: Region

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
