"""Configuration constants and shared pure helpers.

This module centralises the things the writer and the formula builder must
agree on: tab names, the "All" sentinel, the name sanitiser, and the single
column-index-to-letter helper. Keeping them here means a named range and the
SUMIFS reference that points at it always derive from the identical function.
"""

import os
import re
from dataclasses import dataclass

# Tab names. setup and data_source are inputs the user fills in; mapping and the
# three view tabs are generated. Tab matching is case-insensitive (tracker.py),
# so "Setup" and "setup" are treated as the same tab.
SETUP_TAB = "setup"
DATA_SOURCE_TAB = "data_source"
MAPPING_TAB = "mapping"
DAILY_TAB = "daily"
WEEKLY_TAB = "weekly"
MONTHLY_TAB = "monthly"
COMPARISON_TAB = "comparison"

# The sentinel written into Mapping row 2 and used as the Frontend dropdown
# default. It means "All" (no filter on this dimension).
SENTINEL = "**"

# Title shown in the Frontend banner.
FRONTEND_TITLE = "Performance Tracker"

# BigQuery audit log of every tracker created. Values come from env vars set on
# the Cloud Run service. An empty dataset means logging is off (local, tests).
BIGQUERY_PROJECT = os.environ.get("BIGQUERY_PROJECT", "")
BIGQUERY_DATASET = os.environ.get("BIGQUERY_DATASET", "")
BIGQUERY_TABLE = os.environ.get("BIGQUERY_TABLE", "trackers")

# Access control. The master sends the caller's identity token; the service
# verifies it and gates on these. ALLOWED_EMAILS / ALLOWED_DOMAIN decide who may
# use the system at all; ADMIN_EMAILS may act on any tracker (others only on
# trackers they created). RATE_LIMIT_PER_MIN is a per-caller cap (0 disables).
ALLOWED_EMAILS = os.environ.get("ALLOWED_EMAILS", "")
ADMIN_EMAILS = os.environ.get("ADMIN_EMAILS", "")
ALLOWED_DOMAIN = os.environ.get("ALLOWED_DOMAIN", "")
RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN", "30"))


@dataclass
class Config:
    """Bundle of the per-run configuration passed to the domain functions."""

    setup_tab: str = SETUP_TAB
    data_source_tab: str = DATA_SOURCE_TAB
    mapping_tab: str = MAPPING_TAB
    daily_tab: str = DAILY_TAB
    weekly_tab: str = WEEKLY_TAB
    monthly_tab: str = MONTHLY_TAB
    comparison_tab: str = COMPARISON_TAB
    sentinel: str = SENTINEL
    frontend_title: str = FRONTEND_TITLE
    bigquery_project: str = BIGQUERY_PROJECT
    bigquery_dataset: str = BIGQUERY_DATASET
    bigquery_table: str = BIGQUERY_TABLE
    allowed_emails: str = ALLOWED_EMAILS
    admin_emails: str = ADMIN_EMAILS
    allowed_domain: str = ALLOWED_DOMAIN
    rate_limit_per_min: int = RATE_LIMIT_PER_MIN


DEFAULT_CONFIG = Config()


def column_to_letter(col):
    """Convert a 1-based column index to its A1 letter.

    The Sheets API addresses columns numerically (0-based grid indices), but
    named ranges and A1 formulas use letters. This is the single helper both
    sides go through. Input is 1-based: 1 -> A, 26 -> Z, 27 -> AA.
    """
    if col < 1:
        raise ValueError("column index must be 1-based and positive, got {}".format(col))
    letters = ""
    while col > 0:
        col, remainder = divmod(col - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def sanitise_name(name):
    """Turn a header into a valid named-range identifier.

    Rules: keep alphanumerics and underscores, replace every other run of
    characters with a single underscore, collapse repeated underscores, strip
    leading and trailing underscores, and guarantee the result starts with a
    letter. This is the one function the named-range writer and the SUMIFS
    formula builder both call, so their outputs always match.
    """
    s = re.sub(r"[^A-Za-z0-9]+", "_", str(name))
    s = re.sub(r"_+", "_", s)
    s = s.strip("_")
    if not s:
        return "Field"
    if not s[0].isalpha():
        # Named ranges must start with a letter. Prefix a stable marker.
        s = "R_" + s
    return s


def a1(tab, ref):
    """Build a quoted A1 reference like 'Data Source'!A2:A.

    Tab names can contain spaces, so always wrap them in single quotes and
    escape any embedded quote by doubling it.
    """
    return "'{}'!{}".format(tab.replace("'", "''"), ref)
