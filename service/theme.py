"""Visual theme for the view tabs and the input tabs.

Navy banners and headers, a periwinkle highlight for calculated columns, white
value cells on a soft grey page, gridlines off, no merged cells anywhere. Each
view tab is a bucket x metric matrix with a filter bar and a KPI strip. All the
colours and layout positions live here so the look can be changed in one place
without touching the domain logic.

Everything in this module is a pure function that returns Google Sheets API
batchUpdate request dicts. Nothing here talks to the network.
"""

from dataclasses import dataclass


def rgb(hex_str):
    """Turn '#RRGGBB' (or 'RRGGBB') into the Sheets API float colour dict."""
    h = hex_str.lstrip("#")
    return {
        "red": int(h[0:2], 16) / 255.0,
        "green": int(h[2:4], 16) / 255.0,
        "blue": int(h[4:6], 16) / 255.0,
    }


# --- palette (navy headers, periwinkle highlight, white cells) ------------

PAGE_BG = rgb("F1F3F4")       # soft grey page (kept)
CARD_BG = rgb("FFFFFF")
BANNER_BG = rgb("1F3864")     # dark navy header
BANNER_TEXT = rgb("FFFFFF")
PERIWINKLE = rgb("8EAADB")    # light-blue highlight (metric captions)
ACCENT = rgb("1F3864")        # navy value text
MUTED_TEXT = rgb("5F6368")
HEADING_TEXT = rgb("202124")
BORDER = rgb("BFBFBF")
FONT = "Arial"


# --- layout (1-based rows, 0-based columns) -------------------------------
#
# A view tab is a bucket x metric matrix. The period (bucket) runs down
# column A; each metric is a column starting at B. A filter bar (one dropdown
# per dimension) sits up top, and a KPI strip shows the dimension-filtered
# grand total of every metric above the per-period matrix.


@dataclass(frozen=True)
class ViewLayout:
    title_row: int = 1
    filter_label_row: int = 3
    filter_dropdown_row: int = 4
    kpi_label_row: int = 6
    kpi_value_row: int = 7
    matrix_header_row: int = 9
    matrix_first_data_row: int = 10


VIEW_LAYOUT = ViewLayout()


def metric_column(i):
    """Grid column index (0-based) of metric i: A is the period, B is metric 0."""
    return 1 + i


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


# --- the full view formatting pass ----------------------------------------


def view_format_requests(sheet_id, num_dimensions, metrics_meta, num_buckets, date_pattern):
    """Return the ordered batchUpdate requests that theme one view tab.

    metrics_meta is a list of (is_calc, number_pattern) in metric-column order.
    Calculated-metric columns are tinted periwinkle to set them apart from raw
    ones. Order matters: cell formats and number formats go in first, borders
    last, because a repeatCell on userEnteredFormat fields would otherwise wipe
    a border set earlier on the same cell. No cell is ever merged.
    """
    lay = VIEW_LAYOUT
    num_metrics = len(metrics_meta)
    last_col = 1 + num_metrics  # period col + one column per metric (exclusive)
    banner_end_col = max(last_col, num_dimensions, 4)
    canvas_end_col = max(banner_end_col, 8)
    canvas_end_row = max(lay.matrix_first_data_row + num_buckets + 2, 20)

    requests = [_hide_gridlines(sheet_id)]

    # Page canvas: soft grey, Arial, near-black text.
    requests.append(
        _format(
            sheet_id, 0, canvas_end_row, 0, canvas_end_col,
            {"backgroundColor": PAGE_BG, "textFormat": _text(10, HEADING_TEXT)},
            "userEnteredFormat(backgroundColor,textFormat)",
        )
    )

    # Title banner: a navy bar (no merge) with white bold text.
    title_r = lay.title_row - 1
    requests.append(
        _format(
            sheet_id, title_r, title_r + 1, 0, banner_end_col,
            {
                "backgroundColor": BANNER_BG,
                "textFormat": _text(16, BANNER_TEXT, bold=True),
                "horizontalAlignment": "LEFT",
                "verticalAlignment": "MIDDLE",
                "padding": {"left": 16},
            },
            "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,padding)",
        )
    )

    # Filter captions (navy) over the dropdown input cells (white).
    if num_dimensions:
        label_r = lay.filter_label_row - 1
        drop_r = lay.filter_dropdown_row - 1
        requests.append(_navy_header(sheet_id, label_r, label_r + 1, 0, num_dimensions))
        requests.append(_white_cell(sheet_id, drop_r, drop_r + 1, 0, num_dimensions))

    # KPI strip: "Totals" plus a navy header per metric, white value cells below.
    kpi_label_r = lay.kpi_label_row - 1
    kpi_value_r = lay.kpi_value_row - 1
    requests.append(_navy_header(sheet_id, kpi_label_r, kpi_label_r + 1, 0, last_col))
    requests.append(_white_cell(sheet_id, kpi_value_r, kpi_value_r + 1, 0, last_col))
    if num_metrics:
        # KPI values are bold navy to read as headline numbers.
        requests.append(
            _format(
                sheet_id, kpi_value_r, kpi_value_r + 1, 1, last_col,
                {"textFormat": _text(11, ACCENT, bold=True)},
                "userEnteredFormat.textFormat",
            )
        )

    # Matrix header row ("Period" + metric names), navy.
    header_r = lay.matrix_header_row - 1
    requests.append(_navy_header(sheet_id, header_r, header_r + 1, 0, last_col))

    # Matrix body: white cells, periwinkle for calculated-metric columns.
    first = lay.matrix_first_data_row - 1
    end = first + num_buckets
    if num_buckets:
        requests.append(_white_cell(sheet_id, first, end, 0, last_col))
        for i, (is_calc, _pattern) in enumerate(metrics_meta):
            if is_calc:
                col = metric_column(i)
                requests.append(
                    _format(
                        sheet_id, first, end, col, col + 1,
                        {"backgroundColor": PERIWINKLE},
                        "userEnteredFormat.backgroundColor",
                    )
                )
        # Number formats: the period column as a date, each metric column and
        # its KPI value cell with the metric's own pattern.
        requests.append(_num_format(sheet_id, first, end, 0, 1, date_pattern))
        for i, (_is_calc, pattern) in enumerate(metrics_meta):
            col = metric_column(i)
            requests.append(_num_format(sheet_id, first, end, col, col + 1, pattern))
            requests.append(_num_format(sheet_id, kpi_value_r, kpi_value_r + 1, col, col + 1, pattern))

    # Column widths and a couple of row heights.
    requests.append(_col_width(sheet_id, 0, 1, 130))
    if num_metrics:
        requests.append(_col_width(sheet_id, 1, last_col, 120))
    requests.append(_row_height(sheet_id, title_r, 48))

    # Borders last so the cell-format pass above does not clear them.
    requests.append(_outer_border(sheet_id, kpi_label_r, kpi_value_r + 1, 0, last_col))
    if num_buckets:
        requests.append(_outer_border(sheet_id, header_r, end, 0, last_col))
    if num_dimensions:
        drop_r = lay.filter_dropdown_row - 1
        requests.append(
            _outer_border(sheet_id, lay.filter_label_row - 1, drop_r + 1, 0, num_dimensions)
        )

    return requests


def line_chart_request(sheet_id, num_metrics, num_buckets, title="Monthly trend"):
    """An addChart request: a line per metric over the period column.

    Domain is column A (the bucket dates); each metric column B.. is a series.
    The header row is included and headerCount=1 so series take their names
    from the matrix header. The chart is anchored to the right of the matrix.
    """
    lay = VIEW_LAYOUT
    header = lay.matrix_header_row - 1  # include header row for series names
    end = (lay.matrix_first_data_row - 1) + num_buckets

    def source(col_start, col_end):
        return {
            "sources": [_grid(sheet_id, header, end, col_start, col_end)]
        }

    series = [
        {
            "series": {"sourceRange": source(metric_column(i), metric_column(i) + 1)},
            "targetAxis": "LEFT_AXIS",
        }
        for i in range(num_metrics)
    ]
    anchor_col = 1 + num_metrics + 1  # one blank column past the last metric
    return {
        "addChart": {
            "chart": {
                "spec": {
                    "title": title,
                    "basicChart": {
                        "chartType": "LINE",
                        "legendPosition": "BOTTOM_LEGEND",
                        "headerCount": 1,
                        "domains": [
                            {"domain": {"sourceRange": source(0, 1)}}
                        ],
                        "series": series,
                    },
                },
                "position": {
                    "overlayPosition": {
                        "anchorCell": {
                            "sheetId": sheet_id,
                            "rowIndex": header,
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
                setup_sheet_id, 0, 1, 0, 2,
                {"backgroundColor": BANNER_BG, "textFormat": _text(10, BANNER_TEXT, bold=True)},
                "userEnteredFormat(backgroundColor,textFormat)",
            )
        )
        requests.append(_freeze_header(setup_sheet_id))
        requests.append(_col_width(setup_sheet_id, 0, 1, 220))
        requests.append(_col_width(setup_sheet_id, 1, 2, 120))
        requests.append(
            _note(setup_sheet_id, 0, 0, "Field must exactly match a header in data_source.")
        )
        requests.append(_note(setup_sheet_id, 0, 1, 'Type is "metric" or "dimension".'))

    if data_source_sheet_id is not None:
        requests.append(_freeze_header(data_source_sheet_id))
        requests.append(
            _note(data_source_sheet_id, 0, 0, "Paste raw data here. Row 1 = headers, row 2+ = data.")
        )

    return requests
