"""Integration tests for build_view / build_views with a fake sheets client."""

from datetime import date

from config import DEFAULT_CONFIG
from tracker import build_view, build_views, date_to_serial


class FakeClient:
    """A fake covering the client surface build_view touches.

    Serves setup rows, data_source headers, and the date column (as serials);
    records batch writes and batch updates for assertions.
    """

    def __init__(self, setup_rows, headers, date_serials, tabs):
        self._setup = setup_rows
        self._headers = headers
        self._date_serials = date_serials
        self._tabs = dict(tabs)  # title -> sheetId
        self.spreadsheet_id = "SHEET"
        self.raw_writes = []
        self.formula_writes = []
        self.batch_updates = []
        self.cleared = []

    def read_range(self, a1_range, unformatted=False):
        low = a1_range.lower()
        if "setup" in low:
            return self._setup
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


def _client(granularity_tab):
    setup = [
        ["Day", "date", "", ""],
        ["Region", "dimension", "", ""],
        ["Spend", "metric", "", "currency"],
        ["Clicks", "metric", "", "number"],
        ["CPC", "metric", "[Spend]/[Clicks]", "currency"],
    ]
    headers = ["Day", "Region", "Spend", "Clicks"]
    serials = [
        date_to_serial(date(2025, 8, 4)),
        date_to_serial(date(2025, 8, 5)),
        date_to_serial(date(2025, 9, 1)),
    ]
    tabs = {
        "setup": 1,
        "data_source": 2,
        granularity_tab: 3,
    }
    return FakeClient(setup, headers, serials, tabs)


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

        # Matrix formulas reference the per-row period cell with EOMONTH bounds.
        matrix = client._find_write(client.formula_writes, "B10")
        assert len(matrix) == 2  # one row per bucket
        assert "EOMONTH(A10,0)" in matrix[0][0]
        assert matrix[1][0].startswith("=SUMIFS(Spend")

    def test_monthly_adds_a_chart(self):
        client = _client(DEFAULT_CONFIG.monthly_tab)
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.monthly_tab, "month")
        added = [
            r
            for batch in client.batch_updates
            for r in batch
            if "addChart" in r
        ]
        assert len(added) == 1

    def test_daily_no_chart_and_day_bounds(self):
        client = _client(DEFAULT_CONFIG.daily_tab)
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.daily_tab, "day")
        added = [
            r
            for batch in client.batch_updates
            for r in batch
            if "addChart" in r
        ]
        assert added == []
        matrix = client._find_write(client.formula_writes, "B10")
        assert "(A10+1)" in matrix[0][0]

    def test_dropdown_seeded_with_sentinel(self):
        client = _client(DEFAULT_CONFIG.weekly_tab)
        build_view(client, DEFAULT_CONFIG, DEFAULT_CONFIG.weekly_tab, "week")
        drop = client._find_write(client.raw_writes, "A4")
        assert drop == [[DEFAULT_CONFIG.sentinel]]  # one dimension: Region
