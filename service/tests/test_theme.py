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


class TestInputTabFormat:
    def _setup_validations(self):
        requests = theme.input_tab_format_requests(7, None)
        return [r["setDataValidation"] for r in requests if "setDataValidation" in r]

    def test_type_column_gets_a_dropdown_of_the_field_types(self):
        dropdowns = [
            v for v in self._setup_validations()
            if v["rule"]["condition"]["type"] == "ONE_OF_LIST"
        ]
        assert len(dropdowns) == 1
        rule = dropdowns[0]
        # Column B (index 1), below the header row.
        assert rule["range"]["startColumnIndex"] == 1
        assert rule["range"]["endColumnIndex"] == 2
        assert rule["range"]["startRowIndex"] == 1
        values = [v["userEnteredValue"] for v in rule["rule"]["condition"]["values"]]
        assert values == ["metric", "dimension", "date", "calculated"]
        # Strict: only the listed types are accepted at entry.
        assert rule["rule"]["strict"] is True

    def test_toggle_columns_keep_their_checkboxes(self):
        checkboxes = [
            v for v in self._setup_validations()
            if v["rule"]["condition"]["type"] == "BOOLEAN"
        ]
        assert len(checkboxes) == 1
        assert checkboxes[0]["range"]["startColumnIndex"] == 4
        assert checkboxes[0]["range"]["endColumnIndex"] == 7

    def test_no_setup_requests_when_setup_not_created(self):
        requests = theme.input_tab_format_requests(None, None)
        assert requests == []


class TestFormulaColumn:
    def _setup_requests(self):
        return theme.input_tab_format_requests(7, None)

    def test_formula_column_is_plain_text(self):
        # Left as a normal cell, Sheets reads a leading '=' as a live formula
        # and rejects the [Field] bracket syntax with a parse error.
        text_fmts = [
            r["repeatCell"] for r in self._setup_requests()
            if "repeatCell" in r
            and r["repeatCell"]["cell"]["userEnteredFormat"]
            .get("numberFormat", {}).get("type") == "TEXT"
        ]
        assert len(text_fmts) == 1
        rng = text_fmts[0]["range"]
        assert rng["startColumnIndex"] == 2  # column C
        assert rng["endColumnIndex"] == 3
        assert rng["startRowIndex"] == 1     # below the header

    def test_formula_column_has_a_hover_note(self):
        notes = [
            r["updateCells"] for r in self._setup_requests()
            if "updateCells" in r
        ]
        col_c = [n for n in notes if n["range"]["startColumnIndex"] == 2]
        assert len(col_c) == 1
        text = col_c[0]["rows"][0]["values"][0]["note"]
        assert "No leading '='" in text
        assert "[Spend]/[Clicks]" in text
