"""Tests for the centralised helpers in config.py."""

import pytest

from config import cell_ref, column_to_letter, sanitise_name


class TestColumnToLetter:
    def test_first_columns(self):
        assert column_to_letter(1) == "A"
        assert column_to_letter(2) == "B"
        assert column_to_letter(26) == "Z"

    def test_double_letters(self):
        assert column_to_letter(27) == "AA"
        assert column_to_letter(28) == "AB"
        assert column_to_letter(52) == "AZ"
        assert column_to_letter(703) == "AAA"

    def test_rejects_zero_and_negative(self):
        with pytest.raises(ValueError):
            column_to_letter(0)
        with pytest.raises(ValueError):
            column_to_letter(-1)


class TestCellRef:
    def test_both_axes_pinned_by_default(self):
        # A fixed input cell every formula on a tab reads, e.g. a slicer.
        assert cell_ref(2, 4) == "$B$4"

    def test_column_only_for_a_row_label_read_across_its_row(self):
        # Drag right holds column A; drag down advances to the next period.
        assert cell_ref(1, 16, pin_row=False) == "$A16"

    def test_row_only_for_a_column_read_across_a_row(self):
        # Drag right walks on to the next metric's column.
        assert cell_ref(2, 27, pin_col=False) == "B$27"

    def test_neither_axis_pinned(self):
        assert cell_ref(3, 9, pin_col=False, pin_row=False) == "C9"

    def test_accepts_an_already_resolved_letter(self):
        assert cell_ref("H", 4, pin_row=False) == "$H4"

    def test_multi_letter_column(self):
        assert cell_ref(27, 3) == "$AA$3"


class TestSanitiseName:
    def test_spaces_become_underscores(self):
        assert sanitise_name("Total Sales") == "Total_Sales"

    def test_special_chars_stripped(self):
        # Trailing run of special chars collapses then strips off.
        assert sanitise_name("Revenue ($)") == "Revenue"
        assert sanitise_name("Cost/Unit") == "Cost_Unit"

    def test_collapses_repeated_underscores(self):
        assert sanitise_name("a   b") == "a_b"
        assert sanitise_name("a___b") == "a_b"

    def test_strips_leading_and_trailing(self):
        assert sanitise_name("  weird  name  ") == "weird_name"
        assert sanitise_name("_leading") == "leading"
        assert sanitise_name("trailing_") == "trailing"

    def test_leading_digit_gets_letter_prefix(self):
        assert sanitise_name("123abc") == "R_123abc"
        assert sanitise_name("2024 Revenue") == "R_2024_Revenue"

    def test_already_valid_unchanged(self):
        assert sanitise_name("Region") == "Region"
        assert sanitise_name("net_profit") == "net_profit"

    def test_empty_and_all_special(self):
        assert sanitise_name("") == "Field"
        assert sanitise_name("$$$") == "Field"
        assert sanitise_name("___") == "Field"
