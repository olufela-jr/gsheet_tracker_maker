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
Field = namedtuple("Field", ["name", "type", "formula", "fmt"])

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


def number_format_pattern(fmt):
    """Sheets number pattern for a format hint; defaults to a plain count."""
    return _NUMBER_FORMATS.get((fmt or "").lower(), "#,##0")


# --- reading setup and data source ----------------------------------------


def _cell(row, i):
    return (row[i].strip() if len(row) > i and row[i] else "")


def read_setup(client, cfg):
    """Read Setup rows below the header into a list of Field tuples.

    Columns: A name, B type, C formula (calculated metrics), D format hint.
    """
    rows = client.read_range(a1(cfg.setup_tab, "A2:D"))
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
            )
        )
    return fields


def metrics_of(fields):
    return [f.name for f in fields if f.type == "metric"]


def dimensions_of(fields):
    return [f.name for f in fields if f.type == "dimension"]


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
    """Fill Mapping with one column per dimension.

    Each column is: header in row 1, the sentinel in row 2, then the distinct
    sorted values of that dimension from Data Source in row 3+. Mapping is
    cleared first.
    """
    fields = read_setup(client, cfg)
    headers = read_data_source_headers(client, cfg)
    dimensions = dimensions_of(fields)
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
    """The per-bucket value for a metric, filtered by the dropdowns."""
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


def _existing_chart_ids(client, sheet_id):
    """Chart ids embedded on a given sheet, so re-runs can delete them first."""
    meta = client.get_spreadsheet()
    for s in meta.get("sheets", []):
        if s.get("properties", {}).get("sheetId") == sheet_id:
            return [c["chartId"] for c in s.get("charts", []) if "chartId" in c]
    return []


def build_view(client, cfg, tab, granularity, fields=None, headers=None, serials=None):
    """Build one themed view tab: a bucket x metric matrix.

    Layout (theme.VIEW_LAYOUT): a title banner, a filter bar with one dropdown
    per dimension, a KPI strip of dimension-filtered grand totals, then a matrix
    with one row per date bucket (day / week / month) and one column per metric.
    Raw metrics are SUMIFS over the bucket; calculated metrics substitute their
    [Field] tokens with bucket-level SUMIFS and wrap in IFERROR. The month view
    also gets a line chart of every metric over time.

    fields / headers / serials may be passed in when building several views in
    one run, so the setup tab and date column are read once, not per view. This
    matters for the Sheets read-request quota.
    """
    if fields is None:
        fields = read_setup(client, cfg)
    if headers is None:
        headers = read_data_source_headers(client, cfg)
    metric_fields = [f for f in fields if f.type == "metric"]
    dimensions = dimensions_of(fields)
    date_name = date_field_of(fields)
    if date_name is None:
        raise ValidationError(
            ["No single date field is declared, so no view can be built."]
        )

    lay = theme.VIEW_LAYOUT
    date_range = sanitise_name(date_name)
    metric_names = [m.name for m in metric_fields]

    # A tracker's date column is read as serials and bucketed for this view.
    if serials is None:
        serials = _read_date_serials(client, cfg, date_name, headers)
    buckets = distinct_buckets(serials, granularity)

    # The dropdown cell for each dimension sits on the filter row; every SUMIFS
    # references that exact cell, so filtering one place drives the whole tab.
    dim_specs = []
    for idx, dim in enumerate(dimensions):
        dropdown_cell = column_to_letter(idx + 1) + str(lay.filter_dropdown_row)
        dim_specs.append((sanitise_name(dim), dropdown_cell))

    # view tabs are generated; create then clear so a re-run starts fresh.
    ensure_tab(client, tab)
    sheet_id = client.get_sheet_id(tab)
    client.clear_range(tab)

    title = "{} - {}".format(cfg.frontend_title, granularity.capitalize())

    # Plain values go in as RAW; formulas as USER_ENTERED so Sheets evaluates
    # them. Bucket dates are written as serials with a date number format.
    raw_data = [
        {"range": a1(tab, "A{}".format(lay.title_row)), "values": [[title]]},
        {
            "range": a1(tab, "A{}".format(lay.kpi_label_row)),
            "values": [["Totals"] + metric_names],
        },
        {
            "range": a1(tab, "A{}".format(lay.matrix_header_row)),
            "values": [["Period"] + metric_names],
        },
    ]
    if dimensions:
        raw_data.append(
            {"range": a1(tab, "A{}".format(lay.filter_label_row)), "values": [list(dimensions)]}
        )
        raw_data.append(
            {
                "range": a1(tab, "A{}".format(lay.filter_dropdown_row)),
                "values": [[cfg.sentinel for _ in dimensions]],
            }
        )
    if buckets:
        raw_data.append(
            {
                "range": a1(tab, "A{}".format(lay.matrix_first_data_row)),
                "values": [[b] for b in buckets],
            }
        )

    formula_data = []
    if metric_names:
        formula_data.append(
            {
                "range": a1(tab, "B{}".format(lay.kpi_value_row)),
                "values": [[_grand_total_formula(m, dim_specs, cfg.sentinel) for m in metric_fields]],
            }
        )
    if buckets and metric_names:
        matrix = []
        for i in range(len(buckets)):
            cell = "A{}".format(lay.matrix_first_data_row + i)
            matrix.append(
                [
                    _bucket_formula(m, date_range, cell, granularity, dim_specs, cfg.sentinel)
                    for m in metric_fields
                ]
            )
        formula_data.append(
            {"range": a1(tab, "B{}".format(lay.matrix_first_data_row)), "values": matrix}
        )

    client.batch_write_values(raw_data, value_input_option="RAW")
    if formula_data:
        client.batch_write_values(formula_data, value_input_option="USER_ENTERED")

    # Theming, then dropdown data validation, then (month only) a fresh chart.
    metrics_meta = [(bool(m.formula), number_format_pattern(m.fmt)) for m in metric_fields]
    date_pattern = MONTH_FORMAT if granularity == "month" else DATE_FORMAT
    requests = theme.view_format_requests(
        sheet_id, len(dimensions), metrics_meta, len(buckets), date_pattern
    )

    drop_row_index = lay.filter_dropdown_row - 1
    requests.append(
        {
            "setDataValidation": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": drop_row_index,
                    "endRowIndex": drop_row_index + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 50,
                }
            }
        }
    )
    for idx, _ in enumerate(dimensions):
        map_col = column_to_letter(idx + 1)
        source = "=" + a1(cfg.mapping_tab, "{c}2:{c}".format(c=map_col))
        requests.append(
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": drop_row_index,
                        "endRowIndex": drop_row_index + 1,
                        "startColumnIndex": idx,
                        "endColumnIndex": idx + 1,
                    },
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
        )

    if granularity == "month":
        for chart_id in _existing_chart_ids(client, sheet_id):
            requests.append({"deleteEmbeddedObject": {"objectId": chart_id}})

    client.batch_update(requests)

    # The chart is added in its own batch: it references the matrix that the
    # prior writes created, and delete-then-add in one batch can race.
    if granularity == "month" and buckets and metric_names:
        client.batch_update(
            [theme.line_chart_request(sheet_id, len(metric_names), len(buckets))]
        )

    return {
        "tab": tab,
        "granularity": granularity,
        "metrics": metric_names,
        "dimensions": dimensions,
        "buckets": len(buckets),
    }


def build_views(client, cfg):
    """Build all three view tabs (daily, weekly, monthly).

    The setup fields, data_source headers, and date column are read once here
    and passed to each view, so building three views is three reads, not nine.
    """
    fields = read_setup(client, cfg)
    headers = read_data_source_headers(client, cfg)
    date_name = date_field_of(fields)
    serials = (
        _read_date_serials(client, cfg, date_name, headers) if date_name else []
    )
    return [
        build_view(client, cfg, tab, granularity, fields, headers, serials)
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
            a1(cfg.setup_tab, "A1:B1"), [["Field", "Type"]], value_input_option="RAW"
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
