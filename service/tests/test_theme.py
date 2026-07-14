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


class TestPrimitives:
    def test_hide_gridlines(self):
        req = theme.hide_gridlines(123)
        assert (
            req["updateSheetProperties"]["properties"]["gridProperties"][
                "hideGridlines"
            ]
            is True
        )

    def test_header_and_value_and_banner_are_repeat_cells(self):
        assert "repeatCell" in theme.header_row(1, 8, 0, 4)
        assert "repeatCell" in theme.value_cells(1, 9, 10, 0, 4)
        assert "repeatCell" in theme.banner(1, 0, 6)

    def test_highlight_col_tints_background(self):
        req = theme.highlight_col(1, 9, 12, 3)
        fmt = req["repeatCell"]["cell"]["userEnteredFormat"]
        assert fmt["backgroundColor"] == theme.HIGHLIGHT
        assert req["repeatCell"]["fields"] == "userEnteredFormat.backgroundColor"

    def test_kpi_values_are_tinted_total_cells(self):
        req = theme.kpi_values(1, 9, 1, 4)
        fmt = req["repeatCell"]["cell"]["userEnteredFormat"]
        assert fmt["backgroundColor"] == theme.HIGHLIGHT
        assert fmt["textFormat"]["bold"] is True

    def test_num_format_sets_number_pattern(self):
        req = theme.num_format(1, 9, 12, 1, 2, "0%")
        cell = req["repeatCell"]["cell"]["userEnteredFormat"]["numberFormat"]
        assert cell["pattern"] == "0%"

    def test_outer_border_updates_borders(self):
        assert "updateBorders" in theme.outer_border(1, 8, 12, 0, 4)


class TestLineChart:
    def test_one_series_per_metric_column(self):
        req = theme.line_chart_request(
            7, metric_cols=[1, 3, 5], header_row_index=8, end_row_index=14,
            anchor_col=7,
        )
        chart = req["addChart"]["chart"]["spec"]["basicChart"]
        assert chart["chartType"] == "LINE"
        assert len(chart["series"]) == 3

    def test_domain_is_period_column(self):
        req = theme.line_chart_request(
            7, metric_cols=[1], header_row_index=8, end_row_index=10, anchor_col=4,
        )
        domain = req["addChart"]["chart"]["spec"]["basicChart"]["domains"][0]
        src = domain["domain"]["sourceRange"]["sources"][0]
        assert src["startColumnIndex"] == 0 and src["endColumnIndex"] == 1
