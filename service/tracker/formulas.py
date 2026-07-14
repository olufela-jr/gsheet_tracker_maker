"""Pure formula and value helpers: no client, unit tested on their own.

Everything here turns plain Python values into Sheets formulas (SUMIFS,
calculated-metric expressions, date-bucket criteria) or normalises raw cell
values (distinct_values, bucket serials). Nothing touches the API.
"""

import re
from datetime import date, timedelta

from config import sanitise_name

# Matches [Field Name] tokens in a calculated field's formula (brackets so
# multi-word names work).
_TOKEN_RE = re.compile(r"\[([^\]]+)\]")


def formula_tokens(formula):
    """Return the distinct [Field] names referenced in a formula, in order."""
    seen = []
    for match in _TOKEN_RE.findall(formula or ""):
        name = match.strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def distinct_values(values):
    """Return sorted, distinct, non-empty values from a flat list.

    Cell values are coerced to stripped strings. Blanks are dropped. Order is
    a plain ascending string sort so the Mapping column is stable run to run.
    """
    seen = set()
    result = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text == "":
            continue
        if text not in seen:
            seen.add(text)
            result.append(text)
    return sorted(result)


def sumifs_expr(metric_range, dimensions, sentinel="**"):
    """The SUMIFS/SUM expression (no leading '=') filtered by the dropdowns.

    For the "All" case we compare against "<>" (not equal to empty) rather than
    the "*" wildcard, because "*" only matches text and would drop numeric and
    date rows.
    """
    if not dimensions:
        return "SUM({})".format(metric_range)
    clauses = []
    for dim_range, cell in dimensions:
        clauses.append(
            '{r}, IF({c}="{s}","<>",{c})'.format(r=dim_range, c=cell, s=sentinel)
        )
    return "SUMIFS({m}, {clauses})".format(m=metric_range, clauses=", ".join(clauses))


def build_sumifs_formula(metric_range, dimensions, sentinel="**"):
    """A standalone SUMIFS/SUM cell formula (grand total, no date bucket)."""
    return "=" + sumifs_expr(metric_range, dimensions, sentinel)


# --- date bucketing --------------------------------------------------------

_SERIAL_BASE = date(1899, 12, 30)  # the Sheets / Excel date epoch


def serial_to_date(serial):
    return _SERIAL_BASE + timedelta(days=int(serial))


def date_to_serial(d):
    return (d - _SERIAL_BASE).days


def bucket_serial(serial, granularity):
    """Map a date serial to its bucket-start serial for day / week / month.

    Week starts on Monday; month on the first. Time components are dropped.
    """
    s = int(float(serial))
    if granularity == "day":
        return s
    d = serial_to_date(s)
    if granularity == "week":
        return date_to_serial(d - timedelta(days=d.weekday()))
    if granularity == "month":
        return date_to_serial(date(d.year, d.month, 1))
    raise ValueError("unknown granularity: {}".format(granularity))


def distinct_buckets(serials, granularity):
    """Sorted, distinct bucket-start serials from raw date serials.

    Non-numeric / blank cells are skipped.
    """
    seen = set()
    out = []
    for value in serials:
        try:
            bucket = bucket_serial(value, granularity)
        except (ValueError, TypeError):
            continue
        if bucket not in seen:
            seen.add(bucket)
            out.append(bucket)
    return sorted(out)


# --- picker-driven period windows --------------------------------------------

# Fiscal years run July to June (the monthly view is one fiscal year, picked
# by its July start year).
FISCAL_YEAR_START_MONTH = 7

# Rows in each view's period matrix: the most periods the pickers can show.
# The matrices are formula-driven windows scoped by each tab's date controls,
# not lists of the dates seen in the data: daily renders the last 14 days of
# the picked range (of the available data while the pickers are blank) and
# weekly up to 6 Monday-start weeks, both newest first; monthly runs the
# picked fiscal year July to June, with months past TODAY() left blank. Rows
# past the picked range blank out via the guard chain in period_next_formula.
PERIOD_ROWS = {"day": 14, "week": 6, "month": 12}

# The expression for the current fiscal year's start year (bare, no '=').
_FY_EXPR = "IF(MONTH(TODAY())>={m},YEAR(TODAY()),YEAR(TODAY())-1)".format(
    m=FISCAL_YEAR_START_MONTH
)


def picker_default_formulas(granularity):
    """Default formulas for a view's date controls.

    Weekly returns a (from, to) pair ending yesterday (today's data is
    usually partial); monthly returns the single current-fiscal-year formula.
    Daily has no defaults: its dropdowns start blank and the matrix falls
    back to the newest available data.
    """
    if granularity == "week":
        return "=TODAY()-28", "=TODAY()-1"
    if granularity == "month":
        return "=" + _FY_EXPR
    raise ValueError("no picker defaults for granularity: {}".format(granularity))


def period_start_formula(granularity, picker, dates_src=None):
    """The matrix's first period cell, derived from the tab's date controls.

    `picker` is the (from, to) cell-ref pair for day/week — the newest period
    sits first, so the start is the picked end date (its week's Monday for
    weekly) — or the fiscal-year cell ref for month (1 July of that year).
    Daily falls back to the newest date in `dates_src` (the Mapping date
    column) while its picker is blank.
    """
    if granularity == "day":
        return '=IF({t}="",MAX({src}),{t})'.format(t=picker[1], src=dates_src)
    if granularity == "week":
        return "={t}-WEEKDAY({t},3)".format(t=picker[1])
    if granularity == "month":
        return "=DATE({y},{m},1)".format(y=picker, m=FISCAL_YEAR_START_MONTH)
    raise ValueError("unknown granularity: {}".format(granularity))


def period_next_formula(granularity, cell, picker, first_cell=None):
    """Each further period cell, derived from `cell` (the row above).

    Daily / weekly step backwards one day / week per row and go blank once
    past the picked start date (weekly includes the week containing it), so
    the matrix is sized to the picked range. While daily's From picker is
    blank the window ends 14 days below `first_cell` (the matrix's effective
    newest date). Monthly steps forwards through the fiscal year and goes
    blank once past the current month, so a current fiscal year reads as the
    year to date and a past one shows all 12 months. The chain is nested
    IFs, so a blank cell above never errors.
    """
    if granularity == "day":
        return ('=IF({c}="","",IF({c}-1<IF({f}="",{first}-13,{f}),"",{c}-1))'
                .format(c=cell, f=picker[0], first=first_cell))
    if granularity == "week":
        return '=IF({c}="","",IF({c}-7<{f}-WEEKDAY({f},3),"",{c}-7))'.format(
            c=cell, f=picker[0]
        )
    if granularity == "month":
        return '=IF({c}="","",IF(EDATE({c},1)>TODAY(),"",EDATE({c},1)))'.format(c=cell)
    raise ValueError("unknown granularity: {}".format(granularity))


def range_guarded(formula, from_cell, to_cell):
    """The cell formula, blanked while either compare date cell is empty."""
    return '=IF(OR({f}="",{t}=""),"",{rest})'.format(
        f=from_cell, t=to_cell, rest=formula[1:]
    )


def blank_guarded(formula, cell):
    """The cell formula, blanked while `cell` is empty (rows past the window)."""
    return '=IF({c}="","",{rest})'.format(c=cell, rest=formula[1:])


def _date_criteria(date_range, cell, granularity):
    """SUMIFS criteria pair(s) selecting the bucket whose start is in `cell`.

    Upper bounds are exclusive ("<") so date-time values on the last day are
    still included.
    """
    if granularity == "day":
        upper = "({c}+1)".format(c=cell)
    elif granularity == "week":
        upper = "({c}+7)".format(c=cell)
    elif granularity == "month":
        upper = "(EOMONTH({c},0)+1)".format(c=cell)
    else:
        raise ValueError("unknown granularity: {}".format(granularity))
    return [date_range, '">="&{c}'.format(c=cell), date_range, '"<"&{u}'.format(u=upper)]


def bucket_sumifs_expr(metric_range, date_range, bucket_cell, granularity, dims, sentinel="**"):
    """SUMIFS expression for a raw metric within one bucket, dropdown-filtered.

    dims is a list of (dimension_named_range, dropdown_cell) tuples.
    """
    parts = [metric_range] + _date_criteria(date_range, bucket_cell, granularity)
    for dim_range, cell in dims:
        parts.append(dim_range)
        parts.append('IF({c}="{s}","<>",{c})'.format(c=cell, s=sentinel))
    return "SUMIFS({})".format(", ".join(parts))


def calc_expr(formula, resolve):
    """The calc expression wrapped in IFERROR, without a leading '='.

    resolve(name) returns the SUMIFS expression for a raw field in the current
    context. Returned bare so it can be embedded (e.g. inside a CHOOSE arm on
    the comparison tab); build_calc_formula just prefixes '='.
    """
    substituted = _TOKEN_RE.sub(lambda m: resolve(m.group(1).strip()), formula)
    return 'IFERROR({}, "")'.format(substituted)


def build_calc_formula(formula, resolve):
    """A calculated-metric cell formula: substitute [Field] tokens, wrap IFERROR.

    IFERROR turns divide-by-zero into a blank rather than #DIV/0!.
    """
    return "=" + calc_expr(formula, resolve)


def _sumifs_between(metric_range, date_range, lower, upper, dim_specs, sentinel):
    """SUMIFS for a raw metric between two date-criteria strings, dropdown-filtered.

    lower / upper are full criteria strings, e.g. '">="&B5' and '"<"&(C5+1)'.
    """
    parts = [metric_range, date_range, lower, date_range, upper]
    for dim_range, cell in dim_specs:
        parts.append(dim_range)
        parts.append('IF({c}="{s}","<>",{c})'.format(c=cell, s=sentinel))
    return "SUMIFS({})".format(", ".join(parts))


def between_expr(metric, date_range, lower, upper, dim_specs, sentinel):
    """Bare expression (no '=') for a metric over a date window; calc-aware."""
    if metric.formula:
        return calc_expr(
            metric.formula,
            lambda n: _sumifs_between(sanitise_name(n), date_range, lower, upper,
                                      dim_specs, sentinel),
        )
    return _sumifs_between(sanitise_name(metric.name), date_range, lower, upper,
                           dim_specs, sentinel)


def between_formula(metric, date_range, lower, upper, dim_specs, sentinel):
    """Cell formula ('=' + expr) for a metric over a date window; calc-aware."""
    return "=" + between_expr(metric, date_range, lower, upper, dim_specs, sentinel)


def grand_total_formula(metric, dim_specs, sentinel):
    """The dimension-filtered grand total for a metric (no date bucket)."""
    if metric.formula:
        return build_calc_formula(
            metric.formula,
            lambda n: sumifs_expr(sanitise_name(n), dim_specs, sentinel),
        )
    return build_sumifs_formula(sanitise_name(metric.name), dim_specs, sentinel)


def bucket_formula(metric, date_range, cell, granularity, dim_specs, sentinel):
    """The per-bucket value for a metric, filtered by the dropdowns.

    `cell` names the bucket-start cell: a period cell in the matrix, or a
    compare-block week/month picker. Either way the SUMIFS selects that bucket.
    """
    if metric.formula:
        return build_calc_formula(
            metric.formula,
            lambda n: bucket_sumifs_expr(
                sanitise_name(n), date_range, cell, granularity, dim_specs, sentinel
            ),
        )
    return "=" + bucket_sumifs_expr(
        sanitise_name(metric.name), date_range, cell, granularity, dim_specs, sentinel
    )


def _breakout_expr(metric_range, dim_range, value_cell, other_dim_specs, sentinel):
    """SUMIFS for a raw metric fixed to one value of the break-out dimension.

    The break-out dimension is pinned to `value_cell` (the row label); the other
    shown dimensions are still filtered by their dropdowns. Not date-bucketed.
    """
    parts = [metric_range, dim_range, value_cell]
    for dim_range_other, cell in other_dim_specs:
        parts.append(dim_range_other)
        parts.append('IF({c}="{s}","<>",{c})'.format(c=cell, s=sentinel))
    return "SUMIFS({})".format(", ".join(parts))


def breakout_formula(metric, dim_range, value_cell, other_dim_specs, sentinel):
    """The break-out cell for a metric at one dimension value."""
    if metric.formula:
        return build_calc_formula(
            metric.formula,
            lambda n: _breakout_expr(
                sanitise_name(n), dim_range, value_cell, other_dim_specs, sentinel
            ),
        )
    return "=" + _breakout_expr(
        sanitise_name(metric.name), dim_range, value_cell, other_dim_specs, sentinel
    )


# --- number formats --------------------------------------------------------

_NUMBER_FORMATS = {"currency": "$#,##0", "percent": "0%", "number": "#,##0"}
DATE_FORMAT = "d-mmm-yyyy"
MONTH_FORMAT = "mmm-yyyy"
# Signed percent for period-on-period deltas: +40% / -12% / 0%.
DELTA_FORMAT = "+0%;-0%;0%"


def number_format_pattern(fmt):
    """Sheets number pattern for a format hint; defaults to a plain count."""
    return _NUMBER_FORMATS.get((fmt or "").lower(), "#,##0")
