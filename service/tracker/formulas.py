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
