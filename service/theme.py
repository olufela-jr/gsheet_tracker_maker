"""Visual theme for the view tabs and the input tabs.

Deep navy banners and headers, a light-blue tint for totals and calculated
columns, white value cells on a soft grey page, gridlines off, no merged cells
anywhere. Each
view tab is a bucket x metric matrix with a filter bar and a KPI strip. All the
colours and layout positions live here so the look can be changed in one place
without touching the domain logic.

Everything in this module is a pure function that returns Google Sheets API
batchUpdate request dicts. Nothing here talks to the network.

Views are laid out dynamically by tracker.build_view (a running row cursor over
stacked blocks: filter bar, KPI strip, an optional compare block, the by-period
matrix, then a break-out table per flagged dimension). tracker owns the row and
column math; this module turns those positions into styled requests via the
small public primitives below.
"""


def rgb(hex_str):
    """Turn '#RRGGBB' (or 'RRGGBB') into the Sheets API float colour dict."""
    h = hex_str.lstrip("#")
    return {
        "red": int(h[0:2], 16) / 255.0,
        "green": int(h[2:4], 16) / 255.0,
        "blue": int(h[4:6], 16) / 255.0,
    }


# --- palette (deep navy headers, light-blue highlight, white cells) --------
# Borrowed from the reference dashboard: #002060 headers, #D9E1F2 total-row
# tint, white value cells on a soft #F3F3F3 page.

PAGE_BG = rgb("F3F3F3")       # soft grey page
CARD_BG = rgb("FFFFFF")
BANNER_BG = rgb("002060")     # deep navy header
BANNER_TEXT = rgb("FFFFFF")
HIGHLIGHT = rgb("D9E1F2")     # light-blue tint (totals, calculated columns)
ACCENT = rgb("002060")        # navy value text
MUTED_TEXT = rgb("5F6368")
HEADING_TEXT = rgb("202124")
BORDER = rgb("BFBFBF")
FONT = "Arial"


# --- low level request builders -------------------------------------------


def _grid(sheet_id, r1, r2, c1, c2):
    return {
        "sheetId": sheet_id,
        "startRowIndex": r1,
        "endRowIndex": r2,
        "startColumnIndex": c1,
        "endColumnIndex": c2,
    }


def _hide_gridlines(sheet_id):
    return {
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {"hideGridlines": True},
            },
            "fields": "gridProperties.hideGridlines",
        }
    }


def _format(sheet_id, r1, r2, c1, c2, fmt, fields):
    return {
        "repeatCell": {
            "range": _grid(sheet_id, r1, r2, c1, c2),
            "cell": {"userEnteredFormat": fmt},
            "fields": fields,
        }
    }



def _outer_border(sheet_id, r1, r2, c1, c2, color=BORDER):
    line = {"style": "SOLID", "color": color}
    return {
        "updateBorders": {
            "range": _grid(sheet_id, r1, r2, c1, c2),
            "top": line,
            "bottom": line,
            "left": line,
            "right": line,
            "innerVertical": line,
        }
    }


def _col_width(sheet_id, start, end, px):
    return {
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "COLUMNS",
                "startIndex": start,
                "endIndex": end,
            },
            "properties": {"pixelSize": px},
            "fields": "pixelSize",
        }
    }


def _row_height(sheet_id, row_index, px):
    return {
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "ROWS",
                "startIndex": row_index,
                "endIndex": row_index + 1,
            },
            "properties": {"pixelSize": px},
            "fields": "pixelSize",
        }
    }


def _text(font_size, color, bold=False):
    return {
        "fontFamily": FONT,
        "fontSize": font_size,
        "foregroundColor": color,
        "bold": bold,
    }


def _num_type(pattern):
    """Infer the Sheets numberFormat type from a pattern string."""
    p = pattern.lower()
    if "%" in pattern:
        return "PERCENT"
    if "$" in pattern:
        return "CURRENCY"
    if "y" in p or "mmm" in p or "d-" in p:
        return "DATE"
    return "NUMBER"


def _num_format(sheet_id, r1, r2, c1, c2, pattern):
    return _format(
        sheet_id, r1, r2, c1, c2,
        {"numberFormat": {"type": _num_type(pattern), "pattern": pattern}},
        "userEnteredFormat.numberFormat",
    )


def _navy_header(sheet_id, r1, r2, c1, c2, size=9):
    """A navy header cell run: white bold text, left aligned, no merge."""
    return _format(
        sheet_id, r1, r2, c1, c2,
        {
            "backgroundColor": BANNER_BG,
            "textFormat": _text(size, BANNER_TEXT, bold=True),
            "verticalAlignment": "MIDDLE",
            "horizontalAlignment": "LEFT",
            "padding": {"left": 8},
        },
        "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,horizontalAlignment,padding)",
    )


def _white_cell(sheet_id, r1, r2, c1, c2):
    """A plain white value cell run."""
    return _format(
        sheet_id, r1, r2, c1, c2,
        {
            "backgroundColor": CARD_BG,
            "textFormat": _text(10, HEADING_TEXT),
            "verticalAlignment": "MIDDLE",
            "horizontalAlignment": "LEFT",
            "padding": {"left": 8},
        },
        "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,horizontalAlignment,padding)",
    )


# --- public formatting primitives -----------------------------------------
#
# tracker computes block positions, then calls these to style them. All take
# 0-based, half-open row/column ranges (endRow / endCol exclusive), matching the
# Sheets grid. The row/column math lives in tracker, not here. Apply borders
# after fills: a repeatCell on userEnteredFormat would otherwise wipe a border.


def hide_gridlines(sheet_id):
    return _hide_gridlines(sheet_id)


def canvas(sheet_id, end_row, end_col):
    """Soft-grey page background with Arial near-black text over the used area."""
    return _format(
        sheet_id, 0, end_row, 0, end_col,
        {"backgroundColor": PAGE_BG, "textFormat": _text(10, HEADING_TEXT)},
        "userEnteredFormat(backgroundColor,textFormat)",
    )


def banner(sheet_id, row, end_col):
    """A navy title bar (no merge) with white bold text on one row."""
    return _format(
        sheet_id, row, row + 1, 0, end_col,
        {
            "backgroundColor": BANNER_BG,
            "textFormat": _text(16, BANNER_TEXT, bold=True),
            "horizontalAlignment": "LEFT",
            "verticalAlignment": "MIDDLE",
            "padding": {"left": 16},
        },
        "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,padding)",
    )


def section_title(sheet_id, row, end_col):
    """A bold navy caption over a by-period / compare / break-out section."""
    return _format(
        sheet_id, row, row + 1, 0, end_col,
        {"textFormat": _text(11, ACCENT, bold=True)},
        "userEnteredFormat.textFormat",
    )


def header_row(sheet_id, row, c1, c2):
    """A navy header run (one row)."""
    return _navy_header(sheet_id, row, row + 1, c1, c2)


def value_cells(sheet_id, r1, r2, c1, c2):
    """A white value-cell block."""
    return _white_cell(sheet_id, r1, r2, c1, c2)


def kpi_values(sheet_id, row, c1, c2):
    """Tinted bold-navy value cells that read as a Total row."""
    return _format(
        sheet_id, row, row + 1, c1, c2,
        {"backgroundColor": HIGHLIGHT, "textFormat": _text(11, ACCENT, bold=True)},
        "userEnteredFormat(backgroundColor,textFormat)",
    )


def highlight_col(sheet_id, r1, r2, col):
    """Tint one column light blue to mark a calculated-metric column."""
    return _format(
        sheet_id, r1, r2, col, col + 1,
        {"backgroundColor": HIGHLIGHT},
        "userEnteredFormat.backgroundColor",
    )


def highlight_cells(sheet_id, r1, r2, c1, c2):
    """The light-blue Total-row tint over a cell run (e.g. a % change row)."""
    return _format(
        sheet_id, r1, r2, c1, c2,
        {"backgroundColor": HIGHLIGHT},
        "userEnteredFormat.backgroundColor",
    )


def num_format(sheet_id, r1, r2, c1, c2, pattern):
    return _num_format(sheet_id, r1, r2, c1, c2, pattern)


def outer_border(sheet_id, r1, r2, c1, c2):
    return _outer_border(sheet_id, r1, r2, c1, c2)


def col_width(sheet_id, start, end, px):
    return _col_width(sheet_id, start, end, px)


def row_height(sheet_id, row, px):
    return _row_height(sheet_id, row, px)


def line_chart_request(sheet_id, metric_cols, header_row_index, end_row_index,
                       anchor_col, title="Trend", domain_col=0, anchor_row=None):
    """An addChart request: a line per series column over a domain column.

    metric_cols are the 0-based grid columns of the series VALUE cells (any
    delta columns are skipped). domain_col is the 0-based domain column (the
    period column A for the views; the period-index column on the comparison
    tab). Series names come from the header row (headerCount=1). Rows span
    header_row_index..end_row_index (exclusive). The chart anchors at
    anchor_row / anchor_col (anchor_row defaults to the header row).
    """
    def source(col_start, col_end):
        return {"sources": [_grid(sheet_id, header_row_index, end_row_index, col_start, col_end)]}

    series = [
        {"series": {"sourceRange": source(col, col + 1)}, "targetAxis": "LEFT_AXIS"}
        for col in metric_cols
    ]
    return {
        "addChart": {
            "chart": {
                "spec": {
                    "title": title,
                    "basicChart": {
                        "chartType": "LINE",
                        "legendPosition": "BOTTOM_LEGEND",
                        "headerCount": 1,
                        "domains": [{"domain": {"sourceRange": source(domain_col, domain_col + 1)}}],
                        "series": series,
                    },
                },
                "position": {
                    "overlayPosition": {
                        "anchorCell": {
                            "sheetId": sheet_id,
                            "rowIndex": header_row_index if anchor_row is None else anchor_row,
                            "columnIndex": anchor_col,
                        }
                    }
                },
            }
        }
    }


def _freeze_header(sheet_id):
    return {
        "updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount",
        }
    }


def _note(sheet_id, row, col, text):
    return {
        "updateCells": {
            "range": _grid(sheet_id, row, row + 1, col, col + 1),
            "rows": [{"values": [{"note": text}]}],
            "fields": "note",
        }
    }


def input_tab_format_requests(setup_sheet_id, data_source_sheet_id):
    """Format the two input tabs, mirroring the old Apps Script setupTemplate.

    setup: a dark banner header on Field/Type, frozen first row, sensible column
    widths, and hover notes. data_source: a frozen header and a hover note. Pure
    function; the service applies these on scaffold so the look stays in code.
    """
    requests = []

    if setup_sheet_id is not None:
        requests.append(
            _format(
                setup_sheet_id, 0, 1, 0, 7,
                {"backgroundColor": BANNER_BG, "textFormat": _text(10, BANNER_TEXT, bold=True)},
                "userEnteredFormat(backgroundColor,textFormat)",
            )
        )
        requests.append(_freeze_header(setup_sheet_id))
        requests.append(_col_width(setup_sheet_id, 0, 1, 220))
        requests.append(_col_width(setup_sheet_id, 1, 2, 120))
        requests.append(_col_width(setup_sheet_id, 4, 7, 120))
        requests.append(
            _note(setup_sheet_id, 0, 0, "Field must exactly match a header in data_source.")
        )
        requests.append(_note(
            setup_sheet_id, 0, 1,
            'Type is "metric", "dimension", "date", or "calculated".'))
        requests.append(
            _note(
                setup_sheet_id, 0, 4,
                "Dimensions only: check to show this dimension as a filter in "
                "the daily/weekly/monthly views. Blank = hidden from the views.",
            )
        )
        requests.append(
            _note(
                setup_sheet_id, 0, 5,
                "Dimensions only: check to add a break-out table (totals per "
                "value of this dimension) to every view. Independent of Show.",
            )
        )
        requests.append(
            _note(
                setup_sheet_id, 0, 6,
                "Dimensions only: check to list this dimension's values in "
                "the mapping tab. Show / Break-out imply it; leave all three "
                "blank to keep a high-cardinality dimension out of Mapping.",
            )
        )
        # Checkboxes down the Show, Break-out and Mapping columns so the
        # toggles are obvious.
        requests.append(
            {
                "setDataValidation": {
                    "range": _grid(setup_sheet_id, 1, 1000, 4, 7),
                    "rule": {
                        "condition": {"type": "BOOLEAN"},
                        "showCustomUi": True,
                        "strict": False,
                    },
                }
            }
        )

    if data_source_sheet_id is not None:
        requests.append(_freeze_header(data_source_sheet_id))
        requests.append(
            _note(data_source_sheet_id, 0, 0, "Paste raw data here. Row 1 = headers, row 2+ = data.")
        )

    return requests
