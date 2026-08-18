"""Tests for the pure domain helpers in tracker.py."""

import pytest

from datetime import date

from config import DEFAULT_CONFIG
from tracker import (
    Field,
    SETUP_HEADERS,
    PERIOD_ROWS,
    ValidationError,
    blank_guarded,
    calc_cell_formula,
    is_calculated,
    label_of,
    labels_of,
    metric_fields_of,
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
    picker_window_criteria,
    range_guarded,
    read_setup,
    setup_columns,
    number_format_pattern,
    sumifs_expr,
    validate,
)


# A Setup header row with the optional Display name column left out. It is the
# default here, so every fixture below doubles as coverage that the column is
# genuinely optional. The full layout is exercised in TestDisplayName.
SETUP_HEADER_NO_DISPLAY = [
    "Field", "Type", "Formula", "Format", "Show in views",
    "Break-out table", "Mapping",
]

SETUP_HEADER = [
    "Field", "Display name", "Type", "Formula", "Format", "Show in views",
    "Break-out table", "Mapping",
]


class FakeReader:
    """Fake client exposing just read_range for validate/read_setup tests."""

    def __init__(self, setup_rows, headers, setup_header=None):
        # Row 1 of Setup is the header row read_setup resolves columns from.
        self._setup = [setup_header or SETUP_HEADER_NO_DISPLAY] + list(setup_rows)
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

    def test_window_sizes(self):
        assert PERIOD_ROWS == {"day": 14, "week": 6, "month": 12}

    def test_picker_defaults_are_rolling_windows(self):
        assert picker_default_formulas("week") == ("=TODAY()-28", "=TODAY()-1")
        assert picker_default_formulas("month") == "=YEAR(TODAY()-1)"
        # Daily has no defaults: its dropdowns start blank.
        with pytest.raises(ValueError):
            picker_default_formulas("day")

    def test_daily_rolls_fourteen_days_ending_yesterday(self):
        assert period_start_formula("day") == "=TODAY()-14"
        assert period_next_formula("day", "A11") == "=A11+1"

    def test_weekly_rolls_six_monday_start_weeks(self):
        assert period_start_formula("week") == (
            "=TODAY()-1-WEEKDAY(TODAY()-1,3)-35"
        )
        assert period_next_formula("week", "A21") == "=A21+7"

    def test_monthly_starts_january_and_blanks_past_yesterday(self):
        # Yesterday's year, so on 1 January the view still reads as the full
        # year that ended yesterday, not an empty new year.
        assert period_start_formula("month") == "=DATE(YEAR(TODAY()-1),1,1)"
        assert period_next_formula("month", "A21") == (
            '=IF(A21="","",IF(EDATE(A21,1)>TODAY()-1,"",EDATE(A21,1)))'
        )

    def test_unknown_granularity_raises(self):
        with pytest.raises(ValueError):
            picker_default_formulas("year")
        with pytest.raises(ValueError):
            period_start_formula("year")
        with pytest.raises(ValueError):
            period_next_formula("year", "A2")
        with pytest.raises(ValueError):
            picker_window_criteria("year", self.PICKERS)

    def test_picker_window_leaves_blank_sides_unbounded(self):
        # The break-out tables' date bounds: a blank picker cell must not
        # error the SUMIFS, it opens that side of the window instead.
        lower, upper = picker_window_criteria("week", self.PICKERS)
        assert lower == '">="&IF($B$3="",0,$B$3)'
        assert upper == '"<"&IF($D$3="",9.9E+307,$D$3+1)'
        assert picker_window_criteria("day", self.PICKERS) == (lower, upper)

    def test_picker_window_month_covers_the_picked_year(self):
        lower, upper = picker_window_criteria("month", "$B$3")
        assert lower == '">="&IF($B$3="",0,DATE($B$3,1,1))'
        assert upper == '"<"&IF($B$3="",9.9E+307,DATE($B$3+1,1,1))'

    def test_blank_guarded_wraps_a_formula(self):
        assert blank_guarded("=SUM(B:B)", "A5") == '=IF(A5="","",SUM(B:B))'

    def test_range_guarded_needs_both_dates(self):
        assert range_guarded("=SUM(B:B)", "$A7", "$B7") == (
            '=IF(OR($A7="",$B7=""),"",SUM(B:B))'
        )


class TestCalculatedFields:
    def test_calc_cell_formula_uses_sibling_cells(self):
        cells = {"Spend": "B7", "Clicks": "C7"}
        assert calc_cell_formula("[Spend]/[Clicks]", cells.__getitem__) == (
            '=IFERROR(B7/C7, "")'
        )

    def test_calculated_type_is_parsed_and_selected(self):
        setup = [
            ["Day", "date", "", "", "", "", ""],
            ["Spend", "metric", "", "currency", "", "", ""],
            ["CPC", "calculated", "[Spend]/[Clicks]", "currency", "", "", ""],
        ]
        fields = read_setup(FakeReader(setup, ["Day", "Spend"]), DEFAULT_CONFIG)
        by_name = {f.name: f for f in fields}
        assert by_name["CPC"].type == "calculated"
        assert is_calculated(by_name["CPC"])
        assert not is_calculated(by_name["Spend"])
        # Calculated fields render alongside metrics, in Setup order.
        assert [f.name for f in metric_fields_of(fields)] == ["Spend", "CPC"]

    def test_only_the_exact_calculated_spelling_counts(self):
        f = Field(name="CPC", display="", type="metric",
                  formula="[Spend]/[Clicks]", fmt="")
        assert not is_calculated(f)


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
        fields = [Field("Day", "", "date", "", ""), Field("Spend", "", "metric", "", "")]
        assert date_field_of(fields) == "Day"

    def test_none_when_missing_or_multiple(self):
        assert date_field_of([Field("Spend", "", "metric", "", "")]) is None
        two = [Field("A", "", "date", "", ""), Field("B", "", "date", "", "")]
        assert date_field_of(two) is None


class TestValidate:
    def _ok_setup(self):
        # name, type, formula, fmt, show
        return [
            ["Day", "date", "", "", ""],
            ["Region", "dimension", "", "", "TRUE"],
            ["Spend", "metric", "", "currency", ""],
            ["Clicks", "metric", "", "number", ""],
            ["CPC", "calculated", "[Spend]/[Clicks]", "currency", ""],
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

    def test_unknown_type_rejected(self):
        setup = [
            ["Day", "date", "", ""],
            ["Spend", "metrc", "", ""],  # typo
        ]
        client = FakeReader(setup, ["Day", "Spend"])
        with pytest.raises(ValidationError) as exc:
            validate(client, DEFAULT_CONFIG)
        assert any("unknown type 'metrc'" in e for e in exc.value.errors)

    def test_calculated_without_formula_rejected(self):
        setup = [
            ["Day", "date", "", ""],
            ["Spend", "metric", "", ""],
            ["CPC", "calculated", "", ""],
        ]
        client = FakeReader(setup, ["Day", "Spend"])
        with pytest.raises(ValidationError) as exc:
            validate(client, DEFAULT_CONFIG)
        assert any("no formula" in e for e in exc.value.errors)

    def test_formula_on_a_metric_rejected(self):
        setup = [
            ["Day", "date", "", ""],
            ["Spend", "metric", "", ""],
            ["CPC", "metric", "[Spend]/[Spend]", ""],
        ]
        client = FakeReader(setup, ["Day", "Spend", "CPC"])
        with pytest.raises(ValidationError) as exc:
            validate(client, DEFAULT_CONFIG)
        assert any("only calculated" in e for e in exc.value.errors)

    def test_calc_spellings_are_not_normalised(self):
        setup = [
            ["Day", "date", "", ""],
            ["Spend", "metric", "", ""],
            ["CPC", "calc", "[Spend]/[Spend]", ""],
        ]
        client = FakeReader(setup, ["Day", "Spend"])
        with pytest.raises(ValidationError) as exc:
            validate(client, DEFAULT_CONFIG)
        assert any("unknown type 'calc'" in e for e in exc.value.errors)

    def test_calculated_referencing_a_dimension_rejected(self):
        setup = [
            ["Day", "date", "", ""],
            ["Region", "dimension", "", ""],
            ["Spend", "metric", "", ""],
            ["Weird", "calculated", "[Spend]/[Region]", ""],
        ]
        client = FakeReader(setup, ["Day", "Region", "Spend"])
        with pytest.raises(ValidationError) as exc:
            validate(client, DEFAULT_CONFIG)
        assert any("not a metric" in e for e in exc.value.errors)

    def test_calculated_type_skips_header_check(self):
        setup = [
            ["Day", "date", "", ""],
            ["Spend", "metric", "", ""],
            ["CPS", "calculated", "[Spend]/[Spend]", ""],
        ]
        client = FakeReader(setup, ["Day", "Spend"])  # CPS not a header: fine
        validate(client, DEFAULT_CONFIG)  # no raise

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
        setup = [["Day", "date", "", ""], ["X", "calculated", "[Nope]", ""]]
        client = FakeReader(setup, ["Day"])
        with pytest.raises(ValidationError) as exc:
            validate(client, DEFAULT_CONFIG)
        assert any("Nope" in e for e in exc.value.errors)

    def test_calc_referencing_calc_rejected(self):
        setup = [
            ["Day", "date", "", ""],
            ["A", "calculated", "[Day]", ""],
            ["B", "calculated", "[A]", ""],
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
            Field("Region", "", "dimension", "", "", True),
            Field("Channel", "", "dimension", "", "", True),
        ]
        assert dimensions_of(fields) == ["Region", "Channel"]


class TestDisplayName:
    """Setup's Display name column: the label, split from the identity."""

    def test_read_setup_parses_the_display_column(self):
        setup = [
            ["Day", "", "date", "", "", "", "", ""],
            ["Region", "Market Region", "dimension", "", "", "TRUE", "", ""],
            ["Spend", "Media Spend", "metric", "", "currency", "", "", ""],
            ["Clicks", "", "metric", "", "number", "", "", ""],
        ]
        fields = read_setup(
            FakeReader(setup, ["Day"], setup_header=SETUP_HEADER), DEFAULT_CONFIG)
        by_name = {f.name: f for f in fields}
        assert by_name["Spend"].display == "Media Spend"
        assert by_name["Spend"].type == "metric"
        assert by_name["Spend"].fmt == "currency"
        assert by_name["Region"].show is True
        # A blank display name is not a label of its own.
        assert by_name["Clicks"].display == ""

    def test_label_falls_back_to_the_field_name(self):
        fields = [
            Field("Spend", "Media Spend", "metric", "", "currency"),
            Field("Clicks", "", "metric", "", "number"),
        ]
        assert label_of(fields[0]) == "Media Spend"
        assert label_of(fields[1]) == "Clicks"
        assert labels_of(fields) == {"Spend": "Media Spend", "Clicks": "Clicks"}

    def test_the_column_is_optional(self):
        # Columns are resolved by header, so a Setup tab that never got a
        # Display name column reads exactly as before — Type from B, Formula
        # from C — and every field simply labels itself.
        setup = [
            ["Day", "date", "", "", "", "", ""],
            ["Region", "dimension", "", "", "TRUE", "TRUE", ""],
            ["Spend", "metric", "", "currency", "", "", ""],
        ]
        fields = read_setup(FakeReader(setup, ["Day"]), DEFAULT_CONFIG)
        by_name = {f.name: f for f in fields}
        assert by_name["Region"].type == "dimension"
        assert by_name["Region"].breakout is True
        assert by_name["Spend"].fmt == "currency"
        assert all(f.display == "" for f in fields)
        assert labels_of(fields) == {
            "Day": "Day", "Region": "Region", "Spend": "Spend"}

    def test_columns_follow_the_header_not_the_position(self):
        # A user who moves Display name to the end still gets it read.
        moved = ["Field", "Type", "Formula", "Format", "Show in views",
                 "Break-out table", "Mapping", "Display name"]
        columns = setup_columns(moved)
        assert columns["display"] == 7
        assert columns["type"] == 1
        setup = [["Spend", "metric", "", "currency", "", "", "", "Media Spend"]]
        fields = read_setup(
            FakeReader(setup, ["Spend"], setup_header=moved), DEFAULT_CONFIG)
        assert fields[0].display == "Media Spend"
        assert fields[0].fmt == "currency"

    def test_a_role_no_header_names_is_absent(self):
        # Nothing is guessed by position: an unnamed column is an absent one.
        assert setup_columns(SETUP_HEADER_NO_DISPLAY)["display"] is None
        assert setup_columns(SETUP_HEADER)["display"] == 1
        assert setup_columns(["", "", ""]) == {
            role: None for role, _ in SETUP_HEADERS}

    def test_an_unnamed_header_row_is_a_clear_error(self):
        # Reading by header means a header row that was never seeded would
        # otherwise yield zero fields and surface as "No metrics declared".
        setup = [["Spend", "metric", "", "currency", "", "", ""]]
        client = FakeReader(setup, ["Spend"], setup_header=["", "", ""])
        with pytest.raises(ValidationError) as exc:
            read_setup(client, DEFAULT_CONFIG)
        assert any("header row does not name" in e for e in exc.value.errors)
        assert any("'Field'" in e and "'Type'" in e for e in exc.value.errors)

    def test_optional_columns_may_be_dropped_entirely(self):
        # A tracker with no calculated fields and no dimensions needs only
        # Field and Type.
        client = FakeReader(
            [["Day", "date"], ["Spend", "metric"]],
            ["Day", "Spend"],
            setup_header=["Field", "Type"],
        )
        fields = read_setup(client, DEFAULT_CONFIG)
        assert [f.name for f in fields] == ["Day", "Spend"]
        assert all(f.display == "" and f.formula == "" for f in fields)

    def test_duplicate_display_names_are_rejected(self):
        # The Comparison metric picker matches on the label, and Sheets'
        # MATCH ignores case, so two metrics sharing a label would always
        # chart the first of them.
        setup = [
            ["Day", "", "date", "", "", "", "", ""],
            ["Spend", "Cost", "metric", "", "currency", "", "", ""],
            ["Budget", "cost", "metric", "", "currency", "", "", ""],
        ]
        client = FakeReader(setup, ["Day", "Spend", "Budget"],
                            setup_header=SETUP_HEADER)
        with pytest.raises(ValidationError) as exc:
            validate(client, DEFAULT_CONFIG)
        assert any("both display as" in e for e in exc.value.errors)

    def test_a_display_name_may_not_shadow_another_field(self):
        setup = [
            ["Day", "", "date", "", "", "", "", ""],
            ["Spend", "", "metric", "", "currency", "", "", ""],
            ["Budget", "Spend", "metric", "", "currency", "", "", ""],
        ]
        client = FakeReader(setup, ["Day", "Spend", "Budget"],
                            setup_header=SETUP_HEADER)
        with pytest.raises(ValidationError) as exc:
            validate(client, DEFAULT_CONFIG)
        assert any("both display as 'Spend'" in e for e in exc.value.errors)

    def test_duplicate_field_names_are_not_reported_twice(self):
        # Two blank display names colliding is just a duplicate field name,
        # which the name check already reports.
        setup = [
            ["Day", "", "date", "", "", "", "", ""],
            ["Spend", "", "metric", "", "currency", "", "", ""],
            ["Spend", "", "metric", "", "currency", "", "", ""],
        ]
        client = FakeReader(setup, ["Day", "Spend"], setup_header=SETUP_HEADER)
        with pytest.raises(ValidationError) as exc:
            validate(client, DEFAULT_CONFIG)
        assert not any("both display as" in e for e in exc.value.errors)
        assert any("more than once" in e for e in exc.value.errors)

    def test_display_name_does_not_have_to_match_a_header(self):
        # The Field name is what binds to Data Source; the display name is
        # free text and must not be checked against the headers.
        setup = [
            ["Day", "Date", "date", "", "", "", "", ""],
            ["Spend", "Media Spend (£)", "metric", "", "currency", "", "", ""],
        ]
        client = FakeReader(setup, ["Day", "Spend"], setup_header=SETUP_HEADER)
        assert validate(client, DEFAULT_CONFIG)["metrics"] == ["Spend"]


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

    def test_mapping_covers_shown_broken_out_or_flagged(self):
        # Show and Break-out imply a mapping column (their dropdowns and row
        # labels source from it); the Mapping box adds one for a dimension
        # with neither, and a dimension with all three blank stays out of
        # Mapping entirely (e.g. thousands of campaign names).
        setup = [
            ["Day", "date", "", "", "", "", ""],
            ["Region", "dimension", "", "", "TRUE", "TRUE", ""],
            ["Channel", "dimension", "", "", "TRUE", "", ""],
            ["Market", "dimension", "", "", "", "TRUE", ""],
            ["Listed", "dimension", "", "", "", "", "TRUE"],  # flag only
            ["Heavy", "dimension", "", "", "", "", ""],  # stays unmapped
            ["Spend", "metric", "", "", "", "", ""],
        ]
        fields = read_setup(FakeReader(setup, ["Day"]), DEFAULT_CONFIG)
        assert mapping_dimensions_of(fields) == [
            "Region", "Channel", "Market", "Listed"
        ]
        by_name = {f.name: f for f in fields}
        assert by_name["Listed"].mapping is True
        assert by_name["Heavy"].mapping is False


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


class TestCalculatedFormulaEntry:
    """Guardrails for how a calculated formula is typed into the setup tab."""

    def _setup(self, formula):
        return [
            ["Day", "date", "", ""],
            ["Clicks", "metric", "", "number"],
            ["Impressions", "metric", "", "number"],
            ["CTR", "calculated", formula, "percent"],
        ]

    HEADERS = ["Day", "Clicks", "Impressions"]

    def test_leading_equals_rejected(self):
        # Sheets reads a leading '=' as a live formula; left alone it would
        # reach the views as '=IFERROR(=B7/C7, "")'.
        client = FakeReader(self._setup("=[Clicks]/[Impressions]"), self.HEADERS)
        with pytest.raises(ValidationError) as exc:
            validate(client, DEFAULT_CONFIG)
        assert any("starts its formula with '='" in e for e in exc.value.errors)

    def test_wrong_case_token_suggests_the_field(self):
        client = FakeReader(self._setup("[clicks]/[impressions]"), self.HEADERS)
        with pytest.raises(ValidationError) as exc:
            validate(client, DEFAULT_CONFIG)
        assert any(
            "unknown field 'clicks'" in e and "Did you mean [Clicks]?" in e
            for e in exc.value.errors
        )

    def test_formula_without_tokens_rejected(self):
        # A Sheets error value, or plain unbracketed names, would otherwise
        # pass validation and render a dead cell.
        for formula in ("#ERROR!", "clicks/impressions"):
            client = FakeReader(self._setup(formula), self.HEADERS)
            with pytest.raises(ValidationError) as exc:
                validate(client, DEFAULT_CONFIG)
            assert any("no [Field] tokens" in e for e in exc.value.errors)

    def test_correctly_typed_formula_passes(self):
        client = FakeReader(self._setup("[Clicks]/[Impressions]"), self.HEADERS)
        validate(client, DEFAULT_CONFIG)  # no raise
