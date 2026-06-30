"""Visual theme for the Frontend tab.

Dark slate banner, teal accent, white cards on a soft grey page, gridlines
off. Metrics are laid out horizontally as accented tiles. All the colours and
layout positions live here so the look can be changed in one place without
touching the domain logic.

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


# --- palette (dark slate banner, teal accent) -----------------------------

PAGE_BG = rgb("F1F3F4")
CARD_BG = rgb("FFFFFF")
BANNER_BG = rgb("202124")
BANNER_TEXT = rgb("FFFFFF")
ACCENT = rgb("009688")
MUTED_TEXT = rgb("5F6368")
HEADING_TEXT = rgb("202124")
BORDER = rgb("DADCE0")
FONT = "Roboto"


# --- layout (1-based rows, 0-based columns) -------------------------------


@dataclass(frozen=True)
class Layout:
    title_row: int = 1
    filter_label_row: int = 3
    filter_dropdown_row: int = 4
    metric_label_row: int = 7
    metric_value_row: int = 8


LAYOUT = Layout()


def metric_column(i):
    """Metric i sits in every other column so a grey gap separates tiles."""
    return 2 * i


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


def _merge(sheet_id, r1, r2, c1, c2):
    return {"mergeCells": {"range": _grid(sheet_id, r1, r2, c1, c2), "mergeType": "MERGE_ALL"}}


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


# --- the full Frontend formatting pass ------------------------------------


def frontend_format(sheet_id, num_dimensions, num_metrics):
    """Return the ordered batchUpdate requests that theme the Frontend tab.

    Order matters: cell formats and merges go in first, borders last, because
    a repeatCell on userEnteredFormat fields would otherwise wipe a border set
    earlier on the same cell.
    """
    lay = LAYOUT

    # Width of the content area, in columns.
    last_col = 0
    if num_dimensions:
        last_col = max(last_col, num_dimensions - 1)
    if num_metrics:
        last_col = max(last_col, metric_column(num_metrics - 1))
    banner_end_col = max(last_col + 1, 4)
    canvas_end_col = max(banner_end_col, 12)
    canvas_end_row = max(lay.metric_value_row + 4, 20)

    requests = [_hide_gridlines(sheet_id)]

    # Page canvas: soft grey, Roboto, near-black text.
    requests.append(
        _format(
            sheet_id,
            0,
            canvas_end_row,
            0,
            canvas_end_col,
            {"backgroundColor": PAGE_BG, "textFormat": _text(10, HEADING_TEXT)},
            "userEnteredFormat(backgroundColor,textFormat)",
        )
    )

    # Title banner: merged dark slate bar with white bold text.
    title_r = lay.title_row - 1
    requests.append(_merge(sheet_id, title_r, title_r + 1, 0, banner_end_col))
    requests.append(
        _format(
            sheet_id,
            title_r,
            title_r + 1,
            0,
            banner_end_col,
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

    # Filter captions (small grey bold) and the dropdown input cells (white).
    if num_dimensions:
        label_r = lay.filter_label_row - 1
        drop_r = lay.filter_dropdown_row - 1
        requests.append(
            _format(
                sheet_id,
                label_r,
                label_r + 1,
                0,
                num_dimensions,
                {"textFormat": _text(9, MUTED_TEXT, bold=True)},
                "userEnteredFormat(textFormat)",
            )
        )
        requests.append(
            _format(
                sheet_id,
                drop_r,
                drop_r + 1,
                0,
                num_dimensions,
                {
                    "backgroundColor": CARD_BG,
                    "textFormat": _text(10, HEADING_TEXT),
                    "verticalAlignment": "MIDDLE",
                    "horizontalAlignment": "LEFT",
                    "padding": {"left": 8},
                },
                "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,horizontalAlignment,padding)",
            )
        )

    # Metric tiles: a white card per metric, caption above an accented value.
    label_r = lay.metric_label_row - 1
    value_r = lay.metric_value_row - 1
    for i in range(num_metrics):
        col = metric_column(i)
        requests.append(
            _format(
                sheet_id,
                label_r,
                label_r + 1,
                col,
                col + 1,
                {
                    "backgroundColor": CARD_BG,
                    "textFormat": _text(9, MUTED_TEXT, bold=True),
                    "verticalAlignment": "MIDDLE",
                    "horizontalAlignment": "LEFT",
                    "padding": {"left": 12, "top": 6},
                },
                "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,horizontalAlignment,padding)",
            )
        )
        requests.append(
            _format(
                sheet_id,
                value_r,
                value_r + 1,
                col,
                col + 1,
                {
                    "backgroundColor": CARD_BG,
                    "textFormat": _text(20, ACCENT, bold=True),
                    "verticalAlignment": "MIDDLE",
                    "horizontalAlignment": "LEFT",
                    "numberFormat": {"type": "NUMBER", "pattern": "#,##0"},
                    "padding": {"left": 12, "bottom": 8},
                },
                "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,horizontalAlignment,numberFormat,padding)",
            )
        )

    # Column widths and a few row heights for breathing room.
    requests.append(_col_width(sheet_id, 0, banner_end_col, 150))
    requests.append(_row_height(sheet_id, title_r, 56))
    if num_dimensions:
        requests.append(_row_height(sheet_id, lay.filter_label_row - 1, 20))
        requests.append(_row_height(sheet_id, lay.filter_dropdown_row - 1, 30))
    if num_metrics:
        requests.append(_row_height(sheet_id, lay.metric_label_row - 1, 24))
        requests.append(_row_height(sheet_id, lay.metric_value_row - 1, 48))

    # Borders last so the cell-format pass above does not clear them.
    if num_dimensions:
        drop_r = lay.filter_dropdown_row - 1
        requests.append(_outer_border(sheet_id, drop_r, drop_r + 1, 0, num_dimensions))
    for i in range(num_metrics):
        col = metric_column(i)
        requests.append(
            _outer_border(sheet_id, lay.metric_label_row - 1, lay.metric_value_row, col, col + 1)
        )

    return requests


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
