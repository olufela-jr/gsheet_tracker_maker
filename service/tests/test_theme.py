"""Tests for the pure theme helpers."""

import theme


class TestRgb:
    def test_white_and_black(self):
        assert theme.rgb("FFFFFF") == {"red": 1.0, "green": 1.0, "blue": 1.0}
        assert theme.rgb("000000") == {"red": 0.0, "green": 0.0, "blue": 0.0}

    def test_accepts_leading_hash(self):
        assert theme.rgb("#009688") == theme.rgb("009688")

    def test_known_channel(self):
        # 0x80 / 255 is the mid grey channel value.
        assert abs(theme.rgb("808080")["red"] - 128 / 255) < 1e-9


class TestMetricColumn:
    def test_period_then_metrics(self):
        # Column A is the period; metric 0 is column B (index 1).
        assert theme.metric_column(0) == 1
        assert theme.metric_column(1) == 2
        assert theme.metric_column(2) == 3


def _meta(n, calc_indices=()):
    return [(i in calc_indices, "#,##0") for i in range(n)]


class TestViewFormat:
    def test_first_request_hides_gridlines(self):
        requests = theme.view_format_requests(
            123, num_dimensions=2, metrics_meta=_meta(3), num_buckets=5,
            date_pattern="d-mmm-yyyy",
        )
        assert "updateSheetProperties" in requests[0]
        assert (
            requests[0]["updateSheetProperties"]["properties"]["gridProperties"][
                "hideGridlines"
            ]
            is True
        )

    def test_returns_requests_for_empty_tracker(self):
        # No dimensions, metrics, or buckets still themes the page and banner.
        requests = theme.view_format_requests(
            1, num_dimensions=0, metrics_meta=_meta(0), num_buckets=0,
            date_pattern="d-mmm-yyyy",
        )
        assert len(requests) > 0

    def test_no_merges_anywhere(self):
        requests = theme.view_format_requests(
            1, num_dimensions=2, metrics_meta=_meta(3, calc_indices=(2,)),
            num_buckets=4, date_pattern="mmm-yyyy",
        )
        assert not any("mergeCells" in r for r in requests)

    def test_borders_come_after_formats(self):
        requests = theme.view_format_requests(
            1, num_dimensions=1, metrics_meta=_meta(1), num_buckets=3,
            date_pattern="d-mmm-yyyy",
        )
        last_format = max(i for i, r in enumerate(requests) if "repeatCell" in r)
        first_border = min(i for i, r in enumerate(requests) if "updateBorders" in r)
        assert first_border > last_format

    def test_calc_column_gets_periwinkle(self):
        requests = theme.view_format_requests(
            1, num_dimensions=0, metrics_meta=_meta(2, calc_indices=(1,)),
            num_buckets=2, date_pattern="d-mmm-yyyy",
        )
        fills = [
            r["repeatCell"]["cell"]["userEnteredFormat"]["backgroundColor"]
            for r in requests
            if "repeatCell" in r
            and r["repeatCell"]["fields"] == "userEnteredFormat.backgroundColor"
        ]
        assert theme.PERIWINKLE in fills


class TestLineChart:
    def test_line_chart_has_one_series_per_metric(self):
        req = theme.line_chart_request(7, num_metrics=3, num_buckets=6)
        chart = req["addChart"]["chart"]["spec"]["basicChart"]
        assert chart["chartType"] == "LINE"
        assert len(chart["series"]) == 3
