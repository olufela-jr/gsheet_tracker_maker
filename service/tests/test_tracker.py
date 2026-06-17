"""Tests for the pure domain helpers in tracker.py."""

from tracker import build_sumifs_formula, distinct_values


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
