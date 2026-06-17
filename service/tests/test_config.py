"""Tests for the centralised helpers in config.py."""

import pytest

from config import column_to_letter, sanitise_name


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
