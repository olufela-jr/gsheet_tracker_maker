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
    def test_tiles_leave_a_gap_column(self):
        assert theme.metric_column(0) == 0
        assert theme.metric_column(1) == 2
        assert theme.metric_column(2) == 4


class TestFrontendFormat:
    def test_first_request_hides_gridlines(self):
        requests = theme.frontend_format(123, num_dimensions=2, num_metrics=3)
        assert "updateSheetProperties" in requests[0]
        assert (
            requests[0]["updateSheetProperties"]["properties"]["gridProperties"][
                "hideGridlines"
            ]
            is True
        )

    def test_returns_requests_for_empty_tracker(self):
        # No dimensions and no metrics still themes the page and banner.
        requests = theme.frontend_format(1, num_dimensions=0, num_metrics=0)
        assert len(requests) > 0
        assert any("mergeCells" in r for r in requests)

    def test_borders_come_after_formats(self):
        requests = theme.frontend_format(1, num_dimensions=1, num_metrics=1)
        last_format = max(
            i for i, r in enumerate(requests) if "repeatCell" in r
        )
        first_border = min(
            i for i, r in enumerate(requests) if "updateBorders" in r
        )
        assert first_border > last_format
