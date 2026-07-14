"""Tab-level actions: scaffolding a new tracker, mapping, named ranges.

These prepare a spreadsheet for the view builders: the input tabs exist and
are seeded, Mapping carries the distinct dimension values, and every Data
Source column has a named range the SUMIFS formulas can reference.
"""

import theme
from config import column_to_letter, sanitise_name, a1

from .common import read_date_serials
from .fields import (
    ValidationError,
    date_field_of,
    mapping_dimensions_of,
    read_data_source_headers,
    read_setup,
)
from .formulas import DATE_FORMAT, distinct_buckets, distinct_values


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
    in row 2, then the distinct sorted values from Data Source in row 3+.
    After the dimensions comes one column of the distinct dates seen in the
    data (header row 1, serials from row 2, newest first, no sentinel) —
    the views' date dropdowns source from it. Mapping is cleared first.
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

    dates = []
    if date_name is not None:
        serials = read_date_serials(client, cfg, date_name, headers)
        dates = sorted(distinct_buckets(serials, "day"), reverse=True)
        date_col = column_to_letter(len(dimensions) + 1)
        data.append(
            {
                "range": a1(cfg.mapping_tab, "{c}1".format(c=date_col)),
                "majorDimension": "ROWS",
                "values": [[date_name]] + [[s] for s in dates],
            }
        )

    if data:
        client.batch_write_values(data, value_input_option="RAW")

    if dates:
        sheet_id = client.get_sheet_id(cfg.mapping_tab)
        client.batch_update([
            theme.num_format(sheet_id, 1, 1 + len(dates),
                             len(dimensions), len(dimensions) + 1, DATE_FORMAT)
        ])

    return {
        "dimensions": dimensions,
        "columns": len(dimensions),
        "dates": len(dates),
    }


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
