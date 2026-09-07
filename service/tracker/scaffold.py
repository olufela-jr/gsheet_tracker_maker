"""Tab-level actions: scaffolding a new tracker, mapping, named ranges.

These prepare a spreadsheet for the view builders: the input tabs exist and
are seeded, Mapping carries the distinct dimension values, and every Data
Source column has a named range the SUMIFS formulas can reference.
"""

import theme
from config import column_to_letter, sanitise_name, a1

from .fields import (
    SETUP_HEADERS,
    ValidationError,
    date_field_of,
    mapping_dimensions_of,
    read_data_source_headers,
    read_setup,
)
from .formulas import (
    DATE_FORMAT,
    mapping_dates_formula,
    mapping_values_formula,
    mapping_years_formula,
)


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


def ensure_grid(client, title, rows, cols):
    """Grow a tab's grid so it covers at least rows x cols, and return its size.

    addSheet gives a tab Sheets' default 1000 x 26 grid. A view's stacked
    blocks (a break-out table per dimension) can reach past that, and the API
    rejects any request whose range exceeds the grid rather than expanding it.
    Only ever grows: lowering a count would delete the cells beyond it.

    Returns the tab's (rowCount, columnCount) after any growth, so callers can
    clamp tab-wide ranges to it. Returns None for an unknown tab.
    """
    for sheet in client.get_spreadsheet().get("sheets", []):
        props = sheet.get("properties", {})
        if (props.get("title") or "").lower() != title.lower():
            continue
        grid = props.get("gridProperties", {})
        have_rows, have_cols = grid.get("rowCount", 0), grid.get("columnCount", 0)
        want_rows, want_cols = max(rows, have_rows), max(cols, have_cols)
        if (want_rows, want_cols) == (have_rows, have_cols):
            return have_rows, have_cols
        client.batch_update([
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": props.get("sheetId"),
                        "gridProperties": {
                            "rowCount": want_rows,
                            "columnCount": want_cols,
                        },
                    },
                    "fields": "gridProperties.rowCount,gridProperties.columnCount",
                }
            }
        ])
        return want_rows, want_cols
    return None


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


def generate_mapping(client, cfg):
    """Fill Mapping with one column per dimension, plus the available dates.

    Every dimension gets a column regardless of its Show box (Show only
    controls the view slicers). Each column is: header in row 1, the sentinel
    in row 2, then in row 3 a live UNIQUE spill of the Data Source column —
    not hardcoded values — so the list tracks the data between deploys.
    After the dimensions come two generated columns the views' date controls
    source from: the distinct dates seen in the data, then the distinct
    years of those dates (each: header row 1, then a live spill from row 2,
    newest first, no sentinel). Mapping is cleared first.
    """
    fields = read_setup(client, cfg)
    headers = read_data_source_headers(client, cfg)
    dimensions = mapping_dimensions_of(fields)
    date_name = date_field_of(fields)
    header_index = {header: i for i, header in enumerate(headers)}

    # mapping is a generated tab; create it if it does not exist yet.
    ensure_tab(client, cfg.mapping_tab)
    client.clear_range(cfg.mapping_tab)

    data = []
    formulas = []
    for idx, dim in enumerate(dimensions):
        source_col = column_to_letter(header_index[dim] + 1)
        source = a1(cfg.data_source_tab, "{c}2:{c}".format(c=source_col))

        target_col = column_to_letter(idx + 1)
        data.append(
            {
                "range": a1(cfg.mapping_tab, "{c}1".format(c=target_col)),
                "majorDimension": "ROWS",
                "values": [[dim], [cfg.sentinel]],
            }
        )
        formulas.append(
            {
                "range": a1(cfg.mapping_tab, "{c}3".format(c=target_col)),
                "majorDimension": "ROWS",
                "values": [[mapping_values_formula(source)]],
            }
        )

    if date_name is not None:
        date_source_col = column_to_letter(header_index[date_name] + 1)
        source = a1(
            cfg.data_source_tab, "{c}2:{c}".format(c=date_source_col))
        date_col = column_to_letter(len(dimensions) + 1)
        year_col = column_to_letter(len(dimensions) + 2)
        data.append(
            {
                "range": a1(cfg.mapping_tab, "{c}1".format(c=date_col)),
                "majorDimension": "ROWS",
                "values": [[date_name]],
            }
        )
        data.append(
            {
                "range": a1(cfg.mapping_tab, "{c}1".format(c=year_col)),
                "majorDimension": "ROWS",
                "values": [["Year"]],
            }
        )
        formulas.append(
            {
                "range": a1(cfg.mapping_tab, "{c}2".format(c=date_col)),
                "majorDimension": "ROWS",
                "values": [[mapping_dates_formula(source)]],
            }
        )
        formulas.append(
            {
                "range": a1(cfg.mapping_tab, "{c}2".format(c=year_col)),
                "majorDimension": "ROWS",
                "values": [[mapping_years_formula(source)]],
            }
        )

    if data:
        client.batch_write_values(data, value_input_option="RAW")
    if formulas:
        client.batch_write_values(formulas, value_input_option="USER_ENTERED")

    if date_name is not None:
        # The spill's length changes with the data, so the date format runs
        # to the grid bottom rather than a length known at deploy time.
        sheet_id = client.get_sheet_id(cfg.mapping_tab)
        client.batch_update([
            theme.num_format_col(sheet_id, 1, len(dimensions),
                                 len(dimensions) + 1, DATE_FORMAT)
        ])

    return {
        "dimensions": dimensions,
        "columns": len(dimensions),
        "has_dates": date_name is not None,
    }


def _row_count(client, sheet_id):
    """The grid height of a tab, which is where Sheets pins named range ends."""
    for sheet in client.get_spreadsheet().get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("sheetId") == sheet_id:
            return props.get("gridProperties", {}).get("rowCount")
    return None


def _data_source_range(sheet_id, col_index):
    """The GridRange for one Data Source column: <col>2:<col>.

    Grid indices are 0-based and half-open. startRowIndex skips the header.
    We omit endRowIndex to ask for an open-ended range, but be aware that the
    API does not store it that way: whatever we send, Sheets writes back an
    explicit endRowIndex pinned to the tab's current rowCount. That holds for
    addNamedRange, updateNamedRange, and delete-then-re-add alike, and for
    whole-column ranges as much as this one. See _matches_range.
    """
    return {
        "sheetId": sheet_id,
        "startRowIndex": 1,
        "startColumnIndex": col_index,
        "endColumnIndex": col_index + 1,
    }


def _matches_range(current, wanted, row_count):
    """True when an existing named range already has the shape we want.

    Sheets always materialises the open end (see _data_source_range), so a
    range bounded at the tab's current rowCount is as open as one can be and
    must count as a match — otherwise every run re-points all of them, Sheets
    re-caps them, and the next run does it again. A range bounded SHORT of the
    grid is the real defect: the grid grew and the bound did not follow it, so
    rows past the bound have dropped out of the SUMIFS.

    The API omits keys rather than returning nulls, so a zero start index
    comes back absent, hence the default: without it column A would look like
    a mismatch and be rewritten on every run.
    """
    if current is None:
        return False
    end = current.get("endRowIndex")
    if end is not None and end != row_count:
        return False
    for key, value in wanted.items():
        default = 0 if key in ("startRowIndex", "startColumnIndex") else None
        if current.get(key, default) != value:
            return False
    return True


def create_named_ranges(client, cfg):
    """Create or repair one named range per Data Source column.

    Each range covers 'data_source'!<col>2:<col>, which Sheets stores bounded
    at the tab's current row count. The name is the sanitised header. A name
    that already exists is re-pointed when it has drifted — aimed at the wrong
    column, or bounded short of the grid because rows were added since it was
    written. Re-running therefore re-extends the ranges over data loaded since
    the last run, which is why a tracker needs a refresh after new data lands.

    Existing names are matched case-insensitively, because that is how Sheets
    itself scopes them: 'Year' and 'year' are one name, not two. Matching
    exactly would miss the existing range, try to add a second one, and fail
    the whole run on a collision the sheet could never resolve by re-running.
    A range found under a different casing is renamed to the sanitised header,
    which is safe — Sheets resolves references case-insensitively too.
    """
    headers = read_data_source_headers(client, cfg)
    sheet_id = client.get_sheet_id(cfg.data_source_tab)
    if sheet_id is None:
        raise ValueError(
            "Data Source tab '{}' was not found.".format(cfg.data_source_tab)
        )
    row_count = _row_count(client, sheet_id)

    # Keyed by the case-insensitive identity Sheets enforces, not the literal
    # spelling, so a range stored as 'year' is found when looking up 'Year'.
    existing = {n.lower(): nr for n, nr in client.get_named_ranges().items()}
    seen = set()
    requests = []
    created = []
    updated = []
    skipped = []

    for col_index, header in enumerate(headers):
        if header is None or str(header).strip() == "":
            continue
        name = sanitise_name(header)
        key = name.lower()
        # Track names within this batch too, so two headers that sanitise to
        # the same name do not collide.
        if key in seen:
            skipped.append(name)
            continue
        seen.add(key)

        wanted = _data_source_range(sheet_id, col_index)
        current = existing.get(key)
        if current is None:
            requests.append(
                {"addNamedRange": {"namedRange": {"name": name, "range": wanted}}}
            )
            created.append(name)
            continue

        # Repair whichever part has drifted: the range's shape, the spelling,
        # or both. Sending only the changed fields keeps a re-run a no-op.
        named_range = {"namedRangeId": current["namedRangeId"]}
        fields = []
        if not _matches_range(current.get("range"), wanted, row_count):
            named_range["range"] = wanted
            fields.append("range")
        if current.get("name") != name:
            named_range["name"] = name
            fields.append("name")
        if not fields:
            skipped.append(name)
            continue
        requests.append(
            {
                "updateNamedRange": {
                    "namedRange": named_range,
                    "fields": ",".join(fields),
                }
            }
        )
        updated.append(name)

    if requests:
        client.batch_update(requests)

    return {"created": created, "updated": updated, "skipped": skipped}


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
        # Seeded from SETUP_HEADERS, the same list read_setup resolves columns
        # by, so the header we write and the header we parse cannot drift.
        headers = [header for _role, header in SETUP_HEADERS]
        client.write_values(
            a1(cfg.setup_tab, "A1:{}1".format(column_to_letter(len(headers)))),
            [headers],
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
