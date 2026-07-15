"""Tests for the pure domain helpers in tracker.py."""

import pytest

from datetime import date

from config import DEFAULT_CONFIG
from tracker import (
    Field,
    PERIOD_ROWS,
    ValidationError,
    blank_guarded,
    bucket_serial,
    bucket_sumifs_expr,
    build_calc_formula,
    build_sumifs_formula,
    breakout_dimensions_of,
    date_field_of,
    date_to_serial,
    dimensions_of,
    distinct_buckets,
    distinct_values,
    formula_tokens,
    mapping_dimensions_of,
    period_next_formula,
    period_start_formula,
    picker_default_formulas,
    range_guarded,
    read_setup,
    number_format_pattern,
    sumifs_expr,
    validate,
)


class FakeReader:
    """Fake client exposing just read_range for validate/read_setup tests."""

    def __init__(self, setup_rows, headers):
        self._setup = setup_rows  # rows for setup!A2:E
        self._headers = headers

    def read_range(self, a1_range):
        low = a1_range.lower()
        if "setup" in low:
            return self._setup
        if "data_source" in low:
            return [self._headers] if self._headers else []
        return []


class TestBucketing:
    def test_day_bucket_is_same_serial(self):
        s = date_to_serial(date(2025, 8, 19))
        assert bucket_serial(s, "day") == s

    def test_week_bucket_is_monday(self):
        # 2025-08-19 is a Tuesday; its week starts Monday 2025-08-18.
        s = date_to_serial(date(2025, 8, 19))
        assert bucket_serial(s, "week") == date_to_serial(date(2025, 8, 18))

    def test_month_bucket_is_first(self):
        s = date_to_serial(date(2025, 8, 19))
        assert bucket_serial(s, "month") == date_to_serial(date(2025, 8, 1))

    def test_drops_time_component(self):
        s = date_to_serial(date(2025, 8, 19)) + 0.75
        assert bucket_serial(s, "day") == date_to_serial(date(2025, 8, 19))

    def test_distinct_buckets_weekly_and_sorted(self):
        serials = [
            date_to_serial(date(2025, 8, 25)),  # Mon week B
            date_to_serial(date(2025, 8, 19)),  # Tue week A
            date_to_serial(date(2025, 8, 18)),  # Mon week A
            "not-a-date",
        ]
        buckets = distinct_buckets(serials, "week")
        assert buckets == [
            date_to_serial(date(2025, 8, 18)),
            date_to_serial(date(2025, 8, 25)),
        ]


class TestPeriodWindows:
    PICKERS = ("$B$3", "$D$3")
    DATES_SRC = "'mapping'!B2:B"

    def test_window_sizes(self):
        assert PERIOD_ROWS == {"day": 14, "week": 6, "month": 12}

    def test_picker_defaults_are_rolling_windows(self):
        assert picker_default_formulas("week") == ("=TODAY()-28", "=TODAY()-1")
        assert picker_default_formulas("month") == "=YEAR(TODAY())"
        # Daily has no defaults: its dropdowns start blank.
        with pytest.raises(ValueError):
            picker_default_formulas("day")

    def test_daily_falls_back_to_the_newest_available_dates(self):
        assert period_start_formula("day", self.PICKERS, self.DATES_SRC) == (
            "=IF($D$3=\"\",MAX('mapping'!B2:B),$D$3)"
        )
        assert period_next_formula("day", "A11", self.PICKERS, "$A$11") == (
            '=IF(A11="","",IF(A11-1<IF($B$3="",$A$11-13,$B$3),"",A11-1))'
        )

    def test_weekly_runs_monday_starts_back_to_the_start_week(self):
        assert period_start_formula("week", self.PICKERS) == (
            "=$D$3-WEEKDAY($D$3,3)"
        )
        assert period_next_formula("week", "A21", self.PICKERS) == (
            '=IF(A21="","",IF(A21-7<$B$3-WEEKDAY($B$3,3),"",A21-7))'
        )

    def test_monthly_starts_january_and_blanks_past_today(self):
        assert period_start_formula("month", "$B$3") == "=DATE($B$3,1,1)"
        assert period_next_formula("month", "A21", "$B$3") == (
            '=IF(A21="","",IF(EDATE(A21,1)>TODAY(),"",EDATE(A21,1)))'
        )

    def test_unknown_granularity_raises(self):
        with pytest.raises(ValueError):
            picker_default_formulas("year")
        with pytest.raises(ValueError):
            period_start_formula("year", self.PICKERS)
        with pytest.raises(ValueError):
            period_next_formula("year", "A2", self.PICKERS)

    def test_blank_guarded_wraps_a_formula(self):
        assert blank_guarded("=SUM(B:B)", "A5") == '=IF(A5="","",SUM(B:B))'

    def test_range_guarded_needs_both_dates(self):
        assert range_guarded("=SUM(B:B)", "$A7", "$B7") == (
            '=IF(OR($A7="",$B7=""),"",SUM(B:B))'
        )


class TestSumifsExpr:
    def test_no_dims_is_sum(self):
        assert sumifs_expr("Spend", []) == "SUM(Spend)"

    def test_with_dims(self):
        assert sumifs_expr("Spend", [("Region", "B2")]) == (
            'SUMIFS(Spend, Region, IF(B2="**","<>",B2))'
        )

    def test_build_sumifs_formula_prefixes_equals(self):
        assert build_sumifs_formula("Spend", []) == "=SUM(Spend)"


class TestBucketSumifsExpr:
    def test_month_bucket_with_dimension(self):
        expr = bucket_sumifs_expr("Spend", "Day", "A5", "month", [("Region", "B2")])
        assert expr == (
            'SUMIFS(Spend, Day, ">="&A5, Day, "<"&(EOMONTH(A5,0)+1), '
            'Region, IF(B2="**","<>",B2))'
        )

    def test_day_and_week_bounds(self):
        assert '"<"&(A5+1)' in bucket_sumifs_expr("S", "D", "A5", "day", [])
        assert '"<"&(A5+7)' in bucket_sumifs_expr("S", "D", "A5", "week", [])


class TestBuildCalcFormula:
    def test_substitutes_and_wraps_iferror(self):
        formula = build_calc_formula("[Spend]/[Clicks]", lambda n: "X_" + n)
        assert formula == '=IFERROR(X_Spend/X_Clicks, "")'


class TestNumberFormatPattern:
    def test_known_and_default(self):
        assert number_format_pattern("currency") == "$#,##0"
        assert number_format_pattern("percent") == "0%"
        assert number_format_pattern("number") == "#,##0"
        assert number_format_pattern("") == "#,##0"
        assert number_format_pattern("weird") == "#,##0"


class TestFormulaTokens:
    def test_extracts_bracket_tokens(self):
        assert formula_tokens("[Revenue]-[Cost]") == ["Revenue", "Cost"]

    def test_handles_multiword_and_dedupes(self):
        assert formula_tokens("[Ad Spend]/[Clicks]+[Ad Spend]") == ["Ad Spend", "Clicks"]

    def test_empty(self):
        assert formula_tokens("") == []
        assert formula_tokens(None) == []


class TestDateFieldOf:
    def test_single_date(self):
        fields = [Field("Day", "date", "", ""), Field("Spend", "metric", "", "")]
        assert date_field_of(fields) == "Day"

    def test_none_when_missing_or_multiple(self):
        assert date_field_of([Field("Spend", "metric", "", "")]) is None
        two = [Field("A", "date", "", ""), Field("B", "date", "", "")]
        assert date_field_of(two) is None


class TestValidate:
    def _ok_setup(self):
        # name, type, formula, fmt, show
        return [
            ["Day", "date", "", "", ""],
            ["Region", "dimension", "", "", "TRUE"],
            ["Spend", "metric", "", "currency", ""],
            ["Clicks", "metric", "", "number", ""],
            ["CPC", "metric", "[Spend]/[Clicks]", "currency", ""],
        ]

    def test_valid_tracker_passes(self):
        client = FakeReader(self._ok_setup(), ["Day", "Region", "Spend", "Clicks"])
        result = validate(client, DEFAULT_CONFIG)
        assert result["date"] == "Day"
        assert "Spend" in result["metrics"]

    def test_calculated_field_skips_header_check(self):
        # CPC is calculated and has no Data Source column; must not error.
        client = FakeReader(self._ok_setup(), ["Day", "Region", "Spend", "Clicks"])
        validate(client, DEFAULT_CONFIG)  # no raise

    def test_missing_date_field(self):
        setup = [["Spend", "metric", "", ""]]
        client = FakeReader(setup, ["Spend"])
        with pytest.raises(ValidationError) as exc:
            validate(client, DEFAULT_CONFIG)
        assert any("date field" in e for e in exc.value.errors)

    def test_two_date_fields(self):
        setup = [["A", "date", "", ""], ["B", "date", "", ""], ["M", "metric", "", ""]]
        client = FakeReader(setup, ["A", "B", "M"])
        with pytest.raises(ValidationError) as exc:
            validate(client, DEFAULT_CONFIG)
        assert any("exactly one" in e for e in exc.value.errors)

    def test_raw_field_not_a_header(self):
        setup = [["Day", "date", "", ""], ["Ghost", "metric", "", ""]]
        client = FakeReader(setup, ["Day"])  # Ghost missing from headers
        with pytest.raises(ValidationError) as exc:
            validate(client, DEFAULT_CONFIG)
        assert any("Ghost" in e for e in exc.value.errors)

    def test_duplicate_setup_field_name(self):
        # A dimension and the date field sharing a name is ambiguous: the
        # SUMIFS named range can only bind to one column.
        setup = [
            ["week", "date", "", "", ""],
            ["week", "dimension", "", "", "TRUE"],
            ["Spend", "metric", "", "", ""],
        ]
        client = FakeReader(setup, ["week", "Spend"])
        with pytest.raises(ValidationError) as exc:
            validate(client, DEFAULT_CONFIG)
        assert any("declares 'week' more than once" in e for e in exc.value.errors)

    def test_setup_names_colliding_after_sanitising(self):
        setup = [
            ["Day", "date", "", "", ""],
            ["Campaign Name", "dimension", "", "", "TRUE"],
            ["campaign_name", "dimension", "", "", "TRUE"],
            ["Spend", "metric", "", "", ""],
        ]
        client = FakeReader(
            setup, ["Day", "Campaign Name", "campaign_name", "Spend"])
        with pytest.raises(ValidationError) as exc:
            validate(client, DEFAULT_CONFIG)
        assert any("collide" in e and "Campaign Name" in e
                   for e in exc.value.errors)

    def test_duplicate_data_source_header(self):
        setup = [["Day", "date", "", ""], ["Spend", "metric", "", ""]]
        client = FakeReader(setup, ["Day", "Spend", "Day"])
        with pytest.raises(ValidationError) as exc:
            validate(client, DEFAULT_CONFIG)
        assert any("duplicate header 'Day'" in e for e in exc.value.errors)

    def test_calc_referencing_unknown_field(self):
        setup = [["Day", "date", "", ""], ["X", "metric", "[Nope]", ""]]
        client = FakeReader(setup, ["Day"])
        with pytest.raises(ValidationError) as exc:
            validate(client, DEFAULT_CONFIG)
        assert any("Nope" in e for e in exc.value.errors)

    def test_calc_referencing_calc_rejected(self):
        setup = [
            ["Day", "date", "", ""],
            ["A", "metric", "[Day]", ""],
            ["B", "metric", "[A]", ""],
        ]
        client = FakeReader(setup, ["Day"])
        with pytest.raises(ValidationError) as exc:
            validate(client, DEFAULT_CONFIG)
        assert any("another calculated" in e for e in exc.value.errors)


class TestShowToggle:
    def test_read_setup_parses_show_column(self):
        setup = [
            ["Day", "date", "", "", ""],
            ["Region", "dimension", "", "", "TRUE"],
            ["Channel", "dimension", "", "", ""],
            ["Spend", "metric", "", "currency", ""],
        ]
        fields = read_setup(FakeReader(setup, ["Day"]), DEFAULT_CONFIG)
        by_name = {f.name: f for f in fields}
        assert by_name["Region"].show is True
        assert by_name["Channel"].show is False

    def test_hidden_dimension_excluded_from_views(self):
        setup = [
            ["Day", "date", "", "", ""],
            ["Region", "dimension", "", "", "TRUE"],
            ["Channel", "dimension", "", "", ""],  # blank = hidden
            ["Spend", "metric", "", "currency", ""],
        ]
        fields = read_setup(FakeReader(setup, ["Day"]), DEFAULT_CONFIG)
        assert dimensions_of(fields) == ["Region"]

    def test_show_accepts_checkbox_and_typed_affirmatives(self):
        setup = [
            ["Day", "date", "", "", ""],
            ["A", "dimension", "", "", "true"],
            ["B", "dimension", "", "", "x"],
            ["C", "dimension", "", "", "yes"],
            ["D", "dimension", "", "", "FALSE"],
            ["E", "dimension", "", "", "  "],
        ]
        fields = read_setup(FakeReader(setup, ["Day"]), DEFAULT_CONFIG)
        assert dimensions_of(fields) == ["A", "B", "C"]

    def test_dimensions_of_preserves_setup_order(self):
        fields = [
            Field("Region", "dimension", "", "", True),
            Field("Channel", "dimension", "", "", True),
        ]
        assert dimensions_of(fields) == ["Region", "Channel"]


class TestBreakoutColumn:
    def test_read_setup_parses_breakout_column(self):
        setup = [
            ["Day", "date", "", "", "", ""],
            ["Region", "dimension", "", "", "TRUE", "TRUE"],
            ["Channel", "dimension", "", "", "TRUE", ""],
            ["Market", "dimension", "", "", "", "TRUE"],
        ]
        fields = read_setup(FakeReader(setup, ["Day"]), DEFAULT_CONFIG)
        by_name = {f.name: f for f in fields}
        assert by_name["Region"].breakout is True
        assert by_name["Channel"].breakout is False
        assert by_name["Market"].breakout is True

    def test_breakout_is_independent_of_show(self):
        # Market is broken out but not shown; Channel is shown but not broken out.
        setup = [
            ["Day", "date", "", "", "", ""],
            ["Region", "dimension", "", "", "TRUE", "TRUE"],
            ["Channel", "dimension", "", "", "TRUE", ""],
            ["Market", "dimension", "", "", "", "TRUE"],
        ]
        fields = read_setup(FakeReader(setup, ["Day"]), DEFAULT_CONFIG)
        assert dimensions_of(fields) == ["Region", "Channel"]
        assert breakout_dimensions_of(fields) == ["Region", "Market"]

    def test_mapping_covers_every_dimension(self):
        # Mapping is independent of Show/Break-out: every dimension gets a
        # column, so toggling a slicer never reshapes the Mapping tab.
        setup = [
            ["Day", "date", "", "", "", ""],
            ["Region", "dimension", "", "", "TRUE", "TRUE"],
            ["Channel", "dimension", "", "", "TRUE", ""],
            ["Market", "dimension", "", "", "", "TRUE"],
            ["Hidden", "dimension", "", "", "", ""],  # no slicer, still mapped
            ["Spend", "metric", "", "", "", ""],
        ]
        fields = read_setup(FakeReader(setup, ["Day"]), DEFAULT_CONFIG)
        assert mapping_dimensions_of(fields) == [
            "Region", "Channel", "Market", "Hidden"
        ]


class TestDistinctValues:
    def test_sorted_distinct_non_empty(self):
        values = ["b", "a", "b", "c", "a"]
        assert distinct_values(values) == ["a", "b", "c"]

    def test_drops_blanks_and_whitespace(self):
        values = ["a", "", "  ", None, "b", "a"]
        assert distinct_values(values) == ["a", "b"]

    def test_strips_surrounding_whitespace(self):
        # "x " and "x" are the same value once stripped.
        assert distinct_values([" x ", "x"]) == ["x"]

    def test_coerces_non_strings(self):
        assert distinct_values([1, 2, 2, 1]) == ["1", "2"]

    def test_empty_input(self):
        assert distinct_values([]) == []


class TestBuildSumifsFormula:
    def test_zero_dimensions_is_plain_sum(self):
        assert build_sumifs_formula("Sales", []) == "=SUM(Sales)"

    def test_one_dimension(self):
        formula = build_sumifs_formula("Sales", [("Region", "A2")])
        assert formula == '=SUMIFS(Sales, Region, IF(A2="**","<>",A2))'

    def test_multiple_dimensions(self):
        dims = [("Region", "A2"), ("Product", "B2"), ("Channel", "C2")]
        formula = build_sumifs_formula("Sales", dims)
        assert formula == (
            '=SUMIFS(Sales, '
            'Region, IF(A2="**","<>",A2), '
            'Product, IF(B2="**","<>",B2), '
            'Channel, IF(C2="**","<>",C2))'
        )

    def test_uses_not_equal_not_wildcard(self):
        # The All case must use "<>", never "*", so numeric and date columns
        # are not silently dropped.
        formula = build_sumifs_formula("Sales", [("Region", "A2")])
        assert '"<>"' in formula
        assert '"*"' not in formula

    def test_custom_sentinel(self):
        formula = build_sumifs_formula("Sales", [("Region", "A2")], sentinel="ALL")
        assert formula == '=SUMIFS(Sales, Region, IF(A2="ALL","<>",A2))'
