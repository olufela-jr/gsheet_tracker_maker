"""Domain logic for the performance tracker generator.

All functions here are stateless. They take a sheets client and a Config and
do their work against the live sheet. The genuinely pure helpers
(distinct_values, build_sumifs_formula) take no client and are unit tested on
their own.
"""

import re
from collections import namedtuple
from datetime import date, timedelta

import theme
from config import column_to_letter, sanitise_name, a1

# One declared field in the Setup tab.
#   name:    field name (Setup column A)
#   type:    "metric" | "dimension" | "date" (column B)
#   formula: bracket-token expression for a calculated metric, or "" for raw (C)
#   fmt:     "currency" | "percent" | "number" | "" number format hint (D)
#   show:    dimensions only — True shows the dimension as a filter in the
#            daily/weekly/monthly views; blank/False keeps it in the data but
#            hides it from the front end (Setup column E)
#   breakout: dimensions only — True gives the dimension its own break-out
#            table (totals per value) stacked on every view (Setup column F)
Field = namedtuple(
    "Field",
    ["name", "type", "formula", "fmt", "show", "breakout"],
    defaults=(False, False),
)

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


class ValidationError(Exception):
    """Raised when the Setup tab does not describe a usable tracker."""

    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(errors))


# --- pure helpers (unit tested without any API) ---------------------------


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


# --- date bucketing + per-bucket formulas ---------------------------------

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


def build_calc_formula(formula, resolve):
    """Substitute [Field] tokens with expressions and wrap in IFERROR.

    resolve(name) returns the SUMIFS expression for a raw field in the current
    context (a bucket or a grand total). IFERROR turns divide-by-zero into a
    blank rather than #DIV/0!.
    """
    substituted = _TOKEN_RE.sub(lambda m: resolve(m.group(1).strip()), formula)
    return '=IFERROR({}, "")'.format(substituted)


# --- number formats --------------------------------------------------------

_NUMBER_FORMATS = {"currency": "$#,##0", "percent": "0%", "number": "#,##0"}
DATE_FORMAT = "d-mmm-yyyy"
MONTH_FORMAT = "mmm-yyyy"
# Signed percent for period-on-period deltas: +40% / -12% / 0%.
DELTA_FORMAT = "+0%;-0%;0%"


def number_format_pattern(fmt):
    """Sheets number pattern for a format hint; defaults to a plain count."""
    return _NUMBER_FORMATS.get((fmt or "").lower(), "#,##0")


# --- reading setup and data source ----------------------------------------


def _cell(row, i):
    return (row[i].strip() if len(row) > i and row[i] else "")


# Values a Setup "Show" cell may carry: a checkbox (TRUE/FALSE) or a hand-typed
# affirmative. Anything else — including blank — reads as hidden.
_TRUTHY = {"true", "yes", "y", "x", "1", "✓", "on"}


def _truthy(value):
    return value.strip().lower() in _TRUTHY


def read_setup(client, cfg):
    """Read Setup rows below the header into a list of Field tuples.

    Columns: A name, B type, C formula (calculated metrics), D format hint,
    E show (dimensions only — checked shows the dimension as a filter in the
    views, blank hides it), F break-out (dimensions only — checked gives the
    dimension its own totals-per-value table on every view).
    """
    rows = client.read_range(a1(cfg.setup_tab, "A2:F"))
    fields = []
    for row in rows:
        if not row:
            continue
        name = _cell(row, 0)
        if not name:
            continue
        fields.append(
            Field(
                name=name,
                type=_cell(row, 1).lower(),
                formula=_cell(row, 2),
                fmt=_cell(row, 3).lower(),
                show=_truthy(_cell(row, 4)),
                breakout=_truthy(_cell(row, 5)),
            )
        )
    return fields


def metrics_of(fields):
    return [f.name for f in fields if f.type == "metric"]


def dimensions_of(fields):
    """Dimension names that drive the front end, in Setup order.

    Only dimensions with the Show box checked become filter dropdowns and
    mapping columns. An unchecked dimension stays in the data but is hidden
    from the daily/weekly/monthly views, and metrics simply aggregate over all
    its values (no SUMIFS clause for it).
    """
    return [f.name for f in fields if f.type == "dimension" and f.show]


def breakout_dimensions_of(fields):
    """Dimension names that get their own break-out table, in Setup order.

    Independent of Show: a dimension can be a filter, a break-out table, both,
    or neither. A break-out table lists totals per value of the dimension.
    """
    return [f.name for f in fields if f.type == "dimension" and f.breakout]


def mapping_dimensions_of(fields):
    """Dimensions that need a mapping column, in Setup order.

    A mapping column (its distinct values) is needed to drive a filter dropdown
    or to label a break-out table, so it covers shown OR broken-out dimensions.
    """
    return [
        f.name
        for f in fields
        if f.type == "dimension" and (f.show or f.breakout)
    ]


def date_field_of(fields):
    """Return the single date field's name, or None if not exactly one."""
    dates = [f.name for f in fields if f.type == "date"]
    return dates[0] if len(dates) == 1 else None


def read_data_source_headers(client, cfg):
    """Read row 1 of Data Source as a list of header strings."""
    rows = client.read_range(a1(cfg.data_source_tab, "1:1"))
    return rows[0] if rows else []


# --- actions ---------------------------------------------------------------


def require_input_tabs(client, cfg):
    """Raise a clear error if the input tabs are missing.

    A sheet pointed at by URL may not be a prepared tracker. Without this, a
    missing setup/data_source tab surfaces as a raw "Unable to parse range"
    Sheets API error. Here it becomes actionable guidance instead.
    """
    titles = existing_titles(client)
    missing = [
        tab for tab in (cfg.setup_tab, cfg.data_source_tab)
        if tab.lower() not in titles
    ]
    if missing:
        names = " and ".join("'{}'".format(m) for m in missing)
        raise ValidationError(
            [
                "This sheet has no {} tab, so it is not set up as a tracker. "
                "Use 'Set up an existing sheet' (or New tracker) to prepare it."
                .format(names)
            ]
        )


def validate(client, cfg):
    """Check that Setup describes a usable tracker against Data Source.

    Rules: at least one metric; exactly one date field; raw fields (no formula)
    must be Data Source headers, while calculated fields (with a formula) skip
    that check but their [Field] tokens must reference known raw fields.
    """
    fields = read_setup(client, cfg)
    headers = read_data_source_headers(client, cfg)
    metrics = metrics_of(fields)
    dimensions = dimensions_of(fields)
    dates = [f.name for f in fields if f.type == "date"]
    by_name = {f.name: f for f in fields}

    errors = []
    if not metrics:
        errors.append("No metrics declared in Setup.")
    if len(dates) == 0:
        errors.append("No date field. Tag exactly one field with type 'date'.")
    elif len(dates) > 1:
        errors.append("More than one date field; tag exactly one.")
    if not headers:
        errors.append("Data Source has no header row.")

    header_set = set(headers)
    for f in fields:
        if f.formula:
            for token in formula_tokens(f.formula):
                ref = by_name.get(token)
                if ref is None:
                    errors.append(
                        "Calculated field '{}' references unknown field '{}'."
                        .format(f.name, token)
                    )
                elif ref.formula:
                    errors.append(
                        "Calculated field '{}' references another calculated "
                        "field '{}', which is not supported.".format(f.name, token)
                    )
        elif headers and f.name not in header_set:
            errors.append(
                "Setup field '{}' is not a Data Source header.".format(f.name)
            )

    if errors:
        raise ValidationError(errors)

    return {
        "metrics": metrics,
        "dimensions": dimensions,
        "date": dates[0],
        "headers": list(headers),
    }


def generate_mapping(client, cfg):
    """Fill Mapping with one column per dimension that drives the front end.

    A column is created for every dimension that is shown as a filter OR broken
    out into its own table, in Setup order. Each column is: header in row 1, the
    sentinel in row 2, then the distinct sorted values from Data Source in row
    3+. Mapping is cleared first.
    """
    fields = read_setup(client, cfg)
    headers = read_data_source_headers(client, cfg)
    dimensions = mapping_dimensions_of(fields)
    header_index = {header: i for i, header in enumerate(headers)}

    # mapping is a generated tab; create it if it does not exist yet.
    ensure_tab(client, cfg.mapping_tab)
    client.clear_range(cfg.mapping_tab)

    data = []
    for idx, dim in enumerate(dimensions):
        source_col = column_to_letter(header_index[dim] + 1)
        raw_column = client.read_range(
            a1(cfg.data_source_tab, "{c}2:{c}".format(c=source_col))
        )
        flat = [row[0] if row else "" for row in raw_column]
        values = distinct_values(flat)

        target_col = column_to_letter(idx + 1)
        column_cells = [[dim], [cfg.sentinel]] + [[v] for v in values]
        data.append(
            {
                "range": a1(cfg.mapping_tab, "{c}1".format(c=target_col)),
                "majorDimension": "ROWS",
                "values": column_cells,
            }
        )

    if data:
        client.batch_write_values(data, value_input_option="RAW")

    return {"dimensions": dimensions, "columns": len(dimensions)}


def create_named_ranges(client, cfg):
    """Create one named range per Data Source column.

    Each range covers 'Data Source'!<col>2:<col> (header excluded, open-ended
    to the bottom). The name is the sanitised header. Existing names are
    skipped, never overwritten. Grid indices are 0-based and half-open;
    omitting endRowIndex leaves the range open to the bottom of the sheet.
    """
    headers = read_data_source_headers(client, cfg)
    sheet_id = client.get_sheet_id(cfg.data_source_tab)
    if sheet_id is None:
        raise ValueError(
            "Data Source tab '{}' was not found.".format(cfg.data_source_tab)
        )

    existing = set(client.get_named_ranges().keys())
    requests = []
    created = []
    skipped = []

    for col_index, header in enumerate(headers):
        if header is None or str(header).strip() == "":
            continue
        name = sanitise_name(header)
        if name in existing:
            skipped.append(name)
            continue
        # Track names within this batch too, so two headers that sanitise to
        # the same name do not collide.
        existing.add(name)
        requests.append(
            {
                "addNamedRange": {
                    "namedRange": {
                        "name": name,
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "startColumnIndex": col_index,
                            "endColumnIndex": col_index + 1,
                        },
                    }
                }
            }
        )
        created.append(name)

    if requests:
        client.batch_update(requests)

    return {"created": created, "skipped": skipped}


def view_specs(cfg):
    """(tab, granularity) for the three view tabs, in display order."""
    return [
        (cfg.daily_tab, "day"),
        (cfg.weekly_tab, "week"),
        (cfg.monthly_tab, "month"),
    ]


def _read_date_serials(client, cfg, date_name, headers):
    """Read the date column of Data Source as raw serial numbers.

    Reading unformatted means dates come back as serials we can bucket rather
    than as locale-formatted strings.
    """
    if date_name not in headers:
        return []
    col = column_to_letter(headers.index(date_name) + 1)
    rows = client.read_range(
        a1(cfg.data_source_tab, "{c}2:{c}".format(c=col)), unformatted=True
    )
    return [row[0] for row in rows if row]


def _grand_total_formula(metric, dim_specs, sentinel):
    """The dimension-filtered grand total for a metric (no date bucket)."""
    if metric.formula:
        return build_calc_formula(
            metric.formula,
            lambda n: sumifs_expr(sanitise_name(n), dim_specs, sentinel),
        )
    return build_sumifs_formula(sanitise_name(metric.name), dim_specs, sentinel)


def _bucket_formula(metric, date_range, cell, granularity, dim_specs, sentinel):
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


def _breakout_formula(metric, dim_range, value_cell, other_dim_specs, sentinel):
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


def _read_mapping_values(client, cfg, mapping_dims):
    """Read the Mapping tab into {dimension: [distinct values]}.

    Keyed by the header row so it is robust to column order. Row 1 is headers,
    row 2 the sentinel, rows 3+ the values. One read, shared across the views.
    """
    if not mapping_dims:
        return {}
    last = column_to_letter(len(mapping_dims))
    rows = client.read_range(a1(cfg.mapping_tab, "A1:{}".format(last)))
    header_row = rows[0] if rows else []
    col_of = {name: i for i, name in enumerate(header_row)}
    values = {}
    for dim in mapping_dims:
        ci = col_of.get(dim)
        vals = []
        if ci is not None:
            for row in rows[2:]:  # skip header + sentinel
                cell = row[ci] if ci < len(row) else ""
                if cell is not None and str(cell).strip() != "":
                    vals.append(str(cell))
        values[dim] = vals
    return values


def _existing_chart_ids(client, sheet_id):
    """Chart ids embedded on a given sheet, so re-runs can delete them first."""
    meta = client.get_spreadsheet()
    for s in meta.get("sheets", []):
        if s.get("properties", {}).get("sheetId") == sheet_id:
            return [c["chartId"] for c in s.get("charts", []) if "chartId" in c]
    return []


def _grid_dv(sheet_id, r1, r2, c1, c2):
    return {
        "sheetId": sheet_id,
        "startRowIndex": r1,
        "endRowIndex": r2,
        "startColumnIndex": c1,
        "endColumnIndex": c2,
    }


def _one_of_range(sheet_id, row0, col0, source):
    """A ONE_OF_RANGE dropdown on a single cell, sourced from an A1 range."""
    return {
        "setDataValidation": {
            "range": _grid_dv(sheet_id, row0, row0 + 1, col0, col0 + 1),
            "rule": {
                "condition": {
                    "type": "ONE_OF_RANGE",
                    "values": [{"userEnteredValue": source}],
                },
                "showCustomUi": True,
                "strict": False,
            },
        }
    }


def build_view(client, cfg, tab, granularity, fields=None, headers=None,
               serials=None, breakout_values=None):
    """Build one themed view tab as a stack of blocks.

    Top to bottom: a title banner; a filter bar (one dropdown per shown
    dimension); a KPI strip of dimension-filtered grand totals; on the weekly
    and monthly views a compare block (two period pickers with per-metric A, B
    and % change); the by-period matrix (weekly/monthly also carry a % change
    column beside each metric); then one break-out table per flagged dimension
    (totals per value). The monthly view also gets a line chart.

    Positions are computed here with a running row cursor, so blocks that vary
    in height (buckets, dimension values) stack cleanly. fields / headers /
    serials / breakout_values may be passed in when building several views in
    one run, so the inputs are read once, not per view (the read-quota matters).
    """
    if fields is None:
        fields = read_setup(client, cfg)
    if headers is None:
        headers = read_data_source_headers(client, cfg)
    metric_fields = [f for f in fields if f.type == "metric"]
    dimensions = dimensions_of(fields)
    breakouts = breakout_dimensions_of(fields)
    mapping_dims = mapping_dimensions_of(fields)
    date_name = date_field_of(fields)
    if date_name is None:
        raise ValidationError(
            ["No single date field is declared, so no view can be built."]
        )

    sentinel = cfg.sentinel
    date_range = sanitise_name(date_name)
    metric_names = [m.name for m in metric_fields]
    num_metrics = len(metric_fields)

    if serials is None:
        serials = _read_date_serials(client, cfg, date_name, headers)
    buckets = distinct_buckets(serials, granularity)

    has_metrics = num_metrics > 0
    has_buckets = bool(buckets)
    # Compare + per-period deltas live on weekly and monthly, not daily.
    has_compare = granularity in ("week", "month") and has_buckets and has_metrics
    has_delta = has_compare
    mstep = 2 if has_delta else 1  # columns used per metric in the main matrix

    if breakouts and breakout_values is None:
        breakout_values = _read_mapping_values(client, cfg, mapping_dims)
    breakout_values = breakout_values or {}

    ensure_tab(client, tab)
    sheet_id = client.get_sheet_id(tab)
    client.clear_range(tab)

    raw_data = []
    formula_data = []
    fmt = [theme.hide_gridlines(sheet_id)]

    def col(n):  # 1-based column number -> A1 letter
        return column_to_letter(n)

    # ---- Title -----------------------------------------------------------
    title = "{} - {}".format(cfg.frontend_title, granularity.capitalize())
    raw_data.append({"range": a1(tab, "A1"), "values": [[title]]})
    row = 3  # leave row 2 blank

    # ---- Filter bar ------------------------------------------------------
    dim_specs = []  # (named_range, dropdown_cell) for every shown dimension
    filter_block = None
    if dimensions:
        label_row = row
        drop_row = row + 1
        raw_data.append({"range": a1(tab, "A{}".format(label_row)),
                         "values": [list(dimensions)]})
        raw_data.append({"range": a1(tab, "A{}".format(drop_row)),
                         "values": [[sentinel for _ in dimensions]]})
        for i, dim in enumerate(dimensions):
            dim_specs.append((sanitise_name(dim), "{}{}".format(col(i + 1), drop_row)))
        filter_block = (label_row, drop_row)
        row = drop_row + 2

    # ---- KPI strip -------------------------------------------------------
    kpi_block = None
    if has_metrics:
        kpi_label_row = row
        kpi_value_row = row + 1
        raw_data.append({"range": a1(tab, "A{}".format(kpi_label_row)),
                         "values": [["Totals"] + metric_names]})
        formula_data.append({
            "range": a1(tab, "B{}".format(kpi_value_row)),
            "values": [[_grand_total_formula(m, dim_specs, sentinel)
                        for m in metric_fields]],
        })
        kpi_block = (kpi_label_row, kpi_value_row)
        row = kpi_value_row + 2

    # ---- Compare block (weekly / monthly) --------------------------------
    compare_block = None
    if has_compare:
        picker_row = row
        cmp_header_row = row + 1
        cmp_first_row = row + 2
        pa = "B{}".format(picker_row)
        pb = "C{}".format(picker_row)
        raw_data.append({"range": a1(tab, "A{}".format(picker_row)),
                         "values": [["Compare"]]})
        raw_data.append({"range": a1(tab, "B{r}:C{r}".format(r=picker_row)),
                         "values": [[buckets[0], buckets[-1]]]})
        raw_data.append({"range": a1(tab, "A{}".format(cmp_header_row)),
                         "values": [["Metric", "Period A", "Period B", "Change"]]})
        raw_data.append({"range": a1(tab, "A{}".format(cmp_first_row)),
                         "values": [[name] for name in metric_names]})
        cmp_rows = []
        for i, m in enumerate(metric_fields):
            r = cmp_first_row + i
            a_formula = _bucket_formula(m, date_range, pa, granularity, dim_specs, sentinel)
            b_formula = _bucket_formula(m, date_range, pb, granularity, dim_specs, sentinel)
            change = '=IFERROR((C{r}-B{r})/B{r}, "")'.format(r=r)
            cmp_rows.append([a_formula, b_formula, change])
        formula_data.append({"range": a1(tab, "B{}".format(cmp_first_row)),
                             "values": cmp_rows})
        compare_block = (picker_row, cmp_header_row, cmp_first_row)
        row = cmp_first_row + num_metrics + 2

    # ---- Main by-period matrix ------------------------------------------
    main_block = None
    metric_value_cols = []  # 0-based grid cols of metric value cells (for chart)
    if has_metrics:
        main_title_row = row
        main_header_row = row + 1
        main_first_data = row + 2
        raw_data.append({"range": a1(tab, "A{}".format(main_title_row)),
                         "values": [["By {}".format(granularity)]]})
        header = ["Period"]
        for name in metric_names:
            header.append(name)
            if has_delta:
                header.append("change %")
        raw_data.append({"range": a1(tab, "A{}".format(main_header_row)),
                         "values": [header]})
        if has_buckets:
            raw_data.append({"range": a1(tab, "A{}".format(main_first_data)),
                             "values": [[b] for b in buckets]})
            matrix = []
            for j in range(len(buckets)):
                prow = main_first_data + j
                cell = "A{}".format(prow)
                line = []
                for i, m in enumerate(metric_fields):
                    line.append(_bucket_formula(m, date_range, cell, granularity,
                                                dim_specs, sentinel))
                    if has_delta:
                        vc = col(2 + i * mstep)  # value column letter
                        if j == 0:
                            line.append("")
                        else:
                            line.append('=IFERROR(({vc}{r}-{vc}{p})/{vc}{p}, "")'.format(
                                vc=vc, r=prow, p=prow - 1))
                matrix.append(line)
            formula_data.append({"range": a1(tab, "B{}".format(main_first_data)),
                                 "values": matrix})
        metric_value_cols = [1 + i * mstep for i in range(num_metrics)]
        main_block = (main_title_row, main_header_row, main_first_data)
        row = main_first_data + len(buckets) + 2

    # ---- Break-out tables ------------------------------------------------
    breakout_blocks = []
    for bd in breakouts:
        vals = breakout_values.get(bd, [])
        bd_range = sanitise_name(bd)
        other_specs = [spec for dim, spec in zip(dimensions, dim_specs) if dim != bd]
        bo_title_row = row
        bo_header_row = row + 1
        bo_first_data = row + 2
        raw_data.append({"range": a1(tab, "A{}".format(bo_title_row)),
                         "values": [["By {}".format(bd)]]})
        raw_data.append({"range": a1(tab, "A{}".format(bo_header_row)),
                         "values": [[bd] + metric_names]})
        if vals:
            raw_data.append({"range": a1(tab, "A{}".format(bo_first_data)),
                             "values": [[v] for v in vals]})
            block = []
            for k, v in enumerate(vals):
                vcell = "A{}".format(bo_first_data + k)
                block.append([_breakout_formula(m, bd_range, vcell, other_specs, sentinel)
                              for m in metric_fields])
            formula_data.append({"range": a1(tab, "B{}".format(bo_first_data)),
                                 "values": block})
        breakout_blocks.append((bo_header_row, bo_first_data, len(vals)))
        row = bo_first_data + len(vals) + 2

    end_row = row
    client.batch_write_values(raw_data, value_input_option="RAW")
    if formula_data:
        client.batch_write_values(formula_data, value_input_option="USER_ENTERED")

    # ---- Formatting ------------------------------------------------------
    metrics_meta = [(bool(m.formula), number_format_pattern(m.fmt)) for m in metric_fields]
    date_pattern = MONTH_FORMAT if granularity == "month" else DATE_FORMAT
    kpi_last_col = 1 + num_metrics             # period + one column per metric
    main_last_col = 1 + num_metrics * mstep    # metric + its delta on wk/mo
    end_col = max(main_last_col, kpi_last_col, len(dimensions), 4, 8)

    fmt.append(theme.canvas(sheet_id, end_row + 1, end_col))
    fmt.append(theme.banner(sheet_id, 0, end_col))
    fmt.append(theme.row_height(sheet_id, 0, 48))
    fmt.append(theme.col_width(sheet_id, 0, 1, 140))
    fmt.append(theme.col_width(sheet_id, 1, end_col, 110))

    if filter_block:
        label_row, drop_row = filter_block
        fmt.append(theme.header_row(sheet_id, label_row - 1, 0, len(dimensions)))
        fmt.append(theme.value_cells(sheet_id, drop_row - 1, drop_row, 0, len(dimensions)))
        fmt.append(theme.outer_border(sheet_id, label_row - 1, drop_row, 0, len(dimensions)))

    if kpi_block:
        kl, kv = kpi_block
        fmt.append(theme.header_row(sheet_id, kl - 1, 0, kpi_last_col))
        fmt.append(theme.value_cells(sheet_id, kv - 1, kv, 0, kpi_last_col))
        fmt.append(theme.kpi_values(sheet_id, kv - 1, 1, kpi_last_col))
        for i, (_is_calc, pattern) in enumerate(metrics_meta):
            fmt.append(theme.num_format(sheet_id, kv - 1, kv, 1 + i, 2 + i, pattern))
        fmt.append(theme.outer_border(sheet_id, kl - 1, kv, 0, kpi_last_col))

    if compare_block:
        picker_row, cmp_header, cmp_first = compare_block
        cmp_last = 4  # Metric | Period A | Period B | Change
        fmt.append(theme.section_title(sheet_id, picker_row - 1, 1))
        fmt.append(theme.value_cells(sheet_id, picker_row - 1, picker_row, 1, 3))
        fmt.append(theme.num_format(sheet_id, picker_row - 1, picker_row, 1, 3, date_pattern))
        fmt.append(theme.header_row(sheet_id, cmp_header - 1, 0, cmp_last))
        cmp_end = cmp_first - 1 + num_metrics
        fmt.append(theme.value_cells(sheet_id, cmp_first - 1, cmp_end, 0, cmp_last))
        for i, (_is_calc, pattern) in enumerate(metrics_meta):
            rr = cmp_first - 1 + i
            fmt.append(theme.num_format(sheet_id, rr, rr + 1, 1, 3, pattern))
        fmt.append(theme.num_format(sheet_id, cmp_first - 1, cmp_end, 3, 4, DELTA_FORMAT))
        fmt.append(theme.outer_border(sheet_id, cmp_header - 1, cmp_end, 0, cmp_last))

    if main_block:
        mt, mh, mf = main_block
        fmt.append(theme.section_title(sheet_id, mt - 1, main_last_col))
        fmt.append(theme.header_row(sheet_id, mh - 1, 0, main_last_col))
        if has_buckets:
            mend = mf - 1 + len(buckets)
            fmt.append(theme.value_cells(sheet_id, mf - 1, mend, 0, main_last_col))
            fmt.append(theme.num_format(sheet_id, mf - 1, mend, 0, 1, date_pattern))
            for i, (is_calc, pattern) in enumerate(metrics_meta):
                vcol = 1 + i * mstep
                fmt.append(theme.num_format(sheet_id, mf - 1, mend, vcol, vcol + 1, pattern))
                if is_calc:
                    fmt.append(theme.periwinkle_col(sheet_id, mf - 1, mend, vcol))
                if has_delta:
                    fmt.append(theme.num_format(sheet_id, mf - 1, mend, vcol + 1, vcol + 2, DELTA_FORMAT))
            fmt.append(theme.outer_border(sheet_id, mh - 1, mend, 0, main_last_col))

    for (bh, bf, nvals) in breakout_blocks:
        fmt.append(theme.section_title(sheet_id, bh - 2, kpi_last_col))
        fmt.append(theme.header_row(sheet_id, bh - 1, 0, kpi_last_col))
        if nvals:
            bend = bf - 1 + nvals
            fmt.append(theme.value_cells(sheet_id, bf - 1, bend, 0, kpi_last_col))
            for i, (is_calc, pattern) in enumerate(metrics_meta):
                mcol = 1 + i
                fmt.append(theme.num_format(sheet_id, bf - 1, bend, mcol, mcol + 1, pattern))
                if is_calc:
                    fmt.append(theme.periwinkle_col(sheet_id, bf - 1, bend, mcol))
            fmt.append(theme.outer_border(sheet_id, bh - 1, bend, 0, kpi_last_col))

    # ---- Data validations: clear the used area, then add dropdowns -------
    fmt.append({"setDataValidation": {"range": _grid_dv(sheet_id, 0, end_row, 0, 60)}})
    for i, dim in enumerate(dimensions):
        map_col = column_to_letter(mapping_dims.index(dim) + 1)
        source = "=" + a1(cfg.mapping_tab, "{c}2:{c}".format(c=map_col))
        fmt.append(_one_of_range(sheet_id, filter_block[1] - 1, i, source))
    if compare_block and main_block and has_buckets:
        mf = main_block[2]
        period_src = "=" + a1(tab, "A{r1}:A{r2}".format(r1=mf, r2=mf + len(buckets) - 1))
        picker_row0 = compare_block[0] - 1
        fmt.append(_one_of_range(sheet_id, picker_row0, 1, period_src))  # Period A
        fmt.append(_one_of_range(sheet_id, picker_row0, 2, period_src))  # Period B

    if granularity == "month":
        for chart_id in _existing_chart_ids(client, sheet_id):
            fmt.append({"deleteEmbeddedObject": {"objectId": chart_id}})

    client.batch_update(fmt)

    # The chart is added in its own batch: it references the matrix the prior
    # writes created, and delete-then-add in one batch can race.
    if granularity == "month" and main_block and has_buckets and has_metrics:
        _mt, mh, mf = main_block
        header_idx = mh - 1
        end_idx = (mf - 1) + len(buckets)
        anchor = main_last_col + 1
        client.batch_update([
            theme.line_chart_request(sheet_id, metric_value_cols, header_idx, end_idx, anchor)
        ])

    return {
        "tab": tab,
        "granularity": granularity,
        "metrics": metric_names,
        "dimensions": dimensions,
        "breakouts": breakouts,
        "buckets": len(buckets),
    }


def build_views(client, cfg):
    """Build all three view tabs (daily, weekly, monthly).

    Setup fields, data_source headers, the date column, and (when any dimension
    is broken out) the Mapping values are read once here and passed to each
    view, so building three views stays a few reads, not several per view.
    """
    fields = read_setup(client, cfg)
    headers = read_data_source_headers(client, cfg)
    date_name = date_field_of(fields)
    serials = (
        _read_date_serials(client, cfg, date_name, headers) if date_name else []
    )
    breakout_values = (
        _read_mapping_values(client, cfg, mapping_dimensions_of(fields))
        if breakout_dimensions_of(fields)
        else {}
    )
    return [
        build_view(client, cfg, tab, granularity, fields, headers, serials, breakout_values)
        for tab, granularity in view_specs(cfg)
    ]



def existing_titles(client):
    """Map lowercased tab title -> (actual title, sheetId).

    Lowercasing the key makes every tab lookup case-insensitive, matching how
    the Sheets API resolves tab names in A1 ranges.
    """
    meta = client.get_spreadsheet()
    return {
        s["properties"]["title"].lower(): (
            s["properties"]["title"],
            s["properties"]["sheetId"],
        )
        for s in meta.get("sheets", [])
    }


def ensure_tab(client, title):
    """Create a tab if no tab with that name (case-insensitive) exists."""
    if title.lower() not in existing_titles(client):
        client.batch_update([{"addSheet": {"properties": {"title": title}}}])


def scaffold(client, cfg):
    """Ensure the input tabs (setup, data_source) exist on a new tracker sheet.

    Apps Script creates the blank file (so the user owns it) and calls this to
    set up the two tabs the user fills in. setup and data_source are inputs;
    mapping and frontend are created later by the generation steps. When the
    file is brand new (one default sheet) we rename that sheet rather than
    delete it. Any tabs the user already has are left untouched, and an
    existing setup tab is never reseeded.
    """
    wanted = [cfg.setup_tab, cfg.data_source_tab]
    known = {
        t.lower()
        for t in (
            cfg.setup_tab,
            cfg.data_source_tab,
            cfg.mapping_tab,
            cfg.daily_tab,
            cfg.weekly_tab,
            cfg.monthly_tab,
        )
    }
    existing = existing_titles(client)

    missing = [t for t in wanted if t.lower() not in existing]
    leftovers = [
        sheet_id for low, (_, sheet_id) in existing.items() if low not in known
    ]

    requests = []
    reused = 0
    # Reuse a leftover default sheet only when the file is brand new (a single
    # sheet), so we never clobber tabs the user already has.
    if len(existing) == 1 and leftovers and missing:
        requests.append(
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": leftovers[0], "title": missing[0]},
                    "fields": "title",
                }
            }
        )
        reused = 1
    for title in missing[reused:]:
        requests.append({"addSheet": {"properties": {"title": title}}})

    if requests:
        client.batch_update(requests)

    # Seed the setup header only when we just created the setup tab, so an
    # existing setup the user has filled in is never overwritten.
    created_setup = cfg.setup_tab.lower() in {m.lower() for m in missing}
    if created_setup:
        client.write_values(
            a1(cfg.setup_tab, "A1:F1"),
            [["Field", "Type", "Formula", "Format", "Show in views", "Break-out table"]],
            value_input_option="RAW",
        )

    # Format any input tab we just created (header banner, frozen row, widths,
    # notes). Existing tabs are left as the user has them.
    titles = existing_titles(client)
    setup_id = _created_sheet_id(titles, cfg.setup_tab, missing)
    data_source_id = _created_sheet_id(titles, cfg.data_source_tab, missing)
    fmt = theme.input_tab_format_requests(setup_id, data_source_id)
    if fmt:
        client.batch_update(fmt)

    return {
        "spreadsheet_id": client.spreadsheet_id,
        "input_tabs": wanted,
        "created": missing,
    }


def _created_sheet_id(titles, tab, missing):
    """sheetId of a tab we just created (in `missing`), else None.

    Only newly created tabs are formatted, so we never reformat a tab the user
    already had.
    """
    if tab.lower() not in {m.lower() for m in missing}:
        return None
    entry = titles.get(tab.lower())
    return entry[1] if entry else None


# Columns of the BigQuery audit table, in schema order.
TRACKER_FIELDS = [
    "event_id",
    "created_at",
    "spreadsheet_id",
    "url",
    "title",
    "client",
    "sub_brand",
    "created_by",
    "status",
    "service_revision",
]


def build_tracker_record(
    event_id,
    created_at,
    spreadsheet_id,
    url,
    title,
    client,
    sub_brand,
    created_by,
    status="active",
    service_revision="",
):
    """Build the audit row dict, with blanks for any missing value."""
    values = {
        "event_id": event_id,
        "created_at": created_at,
        "spreadsheet_id": spreadsheet_id,
        "url": url,
        "title": title,
        "client": client,
        "sub_brand": sub_brand,
        "created_by": created_by,
        "status": status,
        "service_revision": service_revision,
    }
    return {field: (values.get(field) or "") for field in TRACKER_FIELDS}


def log_tracker(bq_client, cfg, record):
    """Stream one audit row into the BigQuery trackers table."""
    bq_client.insert_row(cfg.bigquery_dataset, cfg.bigquery_table, record)
    return record


def run_all(client, cfg):
    """Run validate, generate_mapping, create_named_ranges, build_views.

    Named ranges are created before the views so the SUMIFS references resolve.
    validate runs first and raises before any writes happen.
    """
    validate(client, cfg)
    mapping = generate_mapping(client, cfg)
    named_ranges = create_named_ranges(client, cfg)
    views = build_views(client, cfg)
    return {
        "mapping": mapping,
        "named_ranges": named_ranges,
        "views": views,
    }
