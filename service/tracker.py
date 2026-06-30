"""Domain logic for the performance tracker generator.

All functions here are stateless. They take a sheets client and a Config and
do their work against the live sheet. The genuinely pure helpers
(distinct_values, build_sumifs_formula) take no client and are unit tested on
their own.
"""

import theme
from config import column_to_letter, sanitise_name, a1


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


def build_sumifs_formula(metric_range, dimensions, sentinel="**"):
    """Build the SUMIFS (or SUM) formula string for one metric.

    metric_range is the sanitised named range for the metric column.
    dimensions is a list of (dimension_named_range, dropdown_cell) tuples in
    dimension order. With zero dimensions there is nothing to filter on, so
    the result is a plain SUM. For the "All" case we compare against "<>"
    (not equal to empty) rather than the "*" wildcard, because "*" only
    matches text and would drop numeric and date rows.
    """
    if not dimensions:
        return "=SUM({})".format(metric_range)
    clauses = []
    for dim_range, cell in dimensions:
        clauses.append(
            '{r}, IF({c}="{s}","<>",{c})'.format(r=dim_range, c=cell, s=sentinel)
        )
    return "=SUMIFS({m}, {clauses})".format(m=metric_range, clauses=", ".join(clauses))


# --- reading setup and data source ----------------------------------------


def read_setup(client, cfg):
    """Read Setup rows below the header into a list of (name, type) tuples."""
    rows = client.read_range(a1(cfg.setup_tab, "A2:B"))
    fields = []
    for row in rows:
        if not row:
            continue
        name = (row[0] or "").strip()
        if not name:
            continue
        field_type = (row[1].strip().lower() if len(row) > 1 and row[1] else "")
        fields.append((name, field_type))
    return fields


def metrics_of(fields):
    return [name for name, field_type in fields if field_type == "metric"]


def dimensions_of(fields):
    return [name for name, field_type in fields if field_type == "dimension"]


def read_data_source_headers(client, cfg):
    """Read row 1 of Data Source as a list of header strings."""
    rows = client.read_range(a1(cfg.data_source_tab, "1:1"))
    return rows[0] if rows else []


# --- actions ---------------------------------------------------------------


def validate(client, cfg):
    """Check that Setup describes a usable tracker against Data Source.

    Errors if there are no metrics, no dimensions, or any declared field is
    missing from the Data Source headers.
    """
    fields = read_setup(client, cfg)
    headers = read_data_source_headers(client, cfg)
    metrics = metrics_of(fields)
    dimensions = dimensions_of(fields)

    errors = []
    if not metrics:
        errors.append("No metrics declared in Setup.")
    if not dimensions:
        errors.append("No dimensions declared in Setup.")

    header_set = set(headers)
    for name, _ in fields:
        if name not in header_set:
            errors.append(
                "Setup field '{}' is not a Data Source header.".format(name)
            )

    if errors:
        raise ValidationError(errors)

    return {"metrics": metrics, "dimensions": dimensions, "headers": list(headers)}


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


def build_frontend(client, cfg):
    """Build the Frontend tab: a themed dashboard.

    Layout (see theme.LAYOUT): a title banner on row 1, a filter bar with one
    dropdown per dimension, then the metrics laid out horizontally as accented
    tiles, each a caption above a SUMIFS value. The dropdown cell of each
    dimension is the cell referenced by every metric's SUMIFS, so they always
    point at the same place.
    """
    fields = read_setup(client, cfg)
    metrics = metrics_of(fields)
    dimensions = dimensions_of(fields)
    lay = theme.LAYOUT

    # frontend is a generated tab; create it if it does not exist yet.
    ensure_tab(client, cfg.frontend_tab)
    front_sheet_id = client.get_sheet_id(cfg.frontend_tab)

    # Clear existing values. This does not touch data validation rules or
    # formatting, so we reset those explicitly in the batch update below.
    client.clear_range(cfg.frontend_tab)

    # Dimension named ranges and the dropdown cell for each dimension. The
    # dropdown sits on the filter row, one column per dimension.
    dim_specs = []
    for idx, dim in enumerate(dimensions):
        dim_range = sanitise_name(dim)
        dropdown_cell = column_to_letter(idx + 1) + str(lay.filter_dropdown_row)
        dim_specs.append((dim_range, dropdown_cell))

    # Plain values go in as RAW; formulas go in as USER_ENTERED so the Sheets
    # API evaluates them instead of storing the text.
    raw_data = [
        {
            "range": a1(cfg.frontend_tab, "A{}".format(lay.title_row)),
            "majorDimension": "ROWS",
            "values": [[cfg.frontend_title]],
        }
    ]
    formula_data = []

    if dimensions:
        raw_data.append(
            {
                "range": a1(cfg.frontend_tab, "A{}".format(lay.filter_label_row)),
                "majorDimension": "ROWS",
                "values": [list(dimensions)],
            }
        )
        raw_data.append(
            {
                "range": a1(cfg.frontend_tab, "A{}".format(lay.filter_dropdown_row)),
                "majorDimension": "ROWS",
                "values": [[cfg.sentinel for _ in dimensions]],
            }
        )

    # Metrics horizontally: tile i sits in column theme.metric_column(i), with
    # a grey gap column between tiles. We write one row of labels and one row
    # of formulas, padding the gap columns with blanks.
    if metrics:
        label_row = []
        value_row = []
        for i, metric in enumerate(metrics):
            if i > 0:
                label_row.append("")
                value_row.append("")
            label_row.append(metric)
            value_row.append(
                build_sumifs_formula(sanitise_name(metric), dim_specs, cfg.sentinel)
            )
        raw_data.append(
            {
                "range": a1(cfg.frontend_tab, "A{}".format(lay.metric_label_row)),
                "majorDimension": "ROWS",
                "values": [label_row],
            }
        )
        formula_data.append(
            {
                "range": a1(cfg.frontend_tab, "A{}".format(lay.metric_value_row)),
                "majorDimension": "ROWS",
                "values": [value_row],
            }
        )

    client.batch_write_values(raw_data, value_input_option="RAW")
    if formula_data:
        client.batch_write_values(formula_data, value_input_option="USER_ENTERED")

    # One batch update for theming plus data validation. Theming first, then
    # the dropdown rules. Start by clearing any old rules across the filter row.
    requests = theme.frontend_format(front_sheet_id, len(dimensions), len(metrics))
    drop_row_index = lay.filter_dropdown_row - 1
    requests.append(
        {
            "setDataValidation": {
                "range": {
                    "sheetId": front_sheet_id,
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
                        "sheetId": front_sheet_id,
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

    client.batch_update(requests)

    return {"metrics": metrics, "dimensions": dimensions}


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
    known = {t.lower() for t in (cfg.setup_tab, cfg.data_source_tab, cfg.mapping_tab, cfg.frontend_tab)}
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
    """Run validate, generate_mapping, create_named_ranges, build_frontend.

    Named ranges are created before the frontend so the SUMIFS references
    resolve. validate runs first and raises before any writes happen.
    """
    validate(client, cfg)
    mapping = generate_mapping(client, cfg)
    named_ranges = create_named_ranges(client, cfg)
    frontend = build_frontend(client, cfg)
    return {
        "mapping": mapping,
        "named_ranges": named_ranges,
        "frontend": frontend,
    }
