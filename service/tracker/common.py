"""Shared plumbing for building front-end tabs (views and comparison).

Page buffers a tab's writes so block builders can lay themselves out with a
running row cursor; the rest are the reads and request helpers every tab
builder needs (date serials, mapping values, dropdown validations, charts).
"""

from config import column_to_letter, a1


class Page:
    """The pending writes and format requests for one tab being built.

    Block builders append to these as they lay themselves out top to bottom:
    `raw` (RAW value writes), `formulas` (USER_ENTERED writes), `fmt` (theme
    requests, applied after the canvas base coat), and `validations` (dropdown
    rules, applied after the tab-wide validation clear). `row` is the running
    1-based row cursor the next block starts from.
    """

    def __init__(self, tab, sheet_id):
        self.tab = tab
        self.sheet_id = sheet_id
        self.raw = []
        self.formulas = []
        self.fmt = []
        self.validations = []
        self.row = 1

    def write(self, ref, values):
        """Queue a RAW value write at an A1 ref (e.g. 'A3' or 'B5:C5')."""
        self.raw.append({"range": a1(self.tab, ref), "values": values})

    def write_formulas(self, ref, values):
        """Queue a USER_ENTERED write (formulas evaluated) at an A1 ref."""
        self.formulas.append({"range": a1(self.tab, ref), "values": values})

    def flush_values(self, client):
        """Send the queued value writes: raw first, then formulas."""
        client.batch_write_values(self.raw, value_input_option="RAW")
        if self.formulas:
            client.batch_write_values(self.formulas, value_input_option="USER_ENTERED")


def read_date_serials(client, cfg, date_name, headers):
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


def read_mapping_values(client, cfg, mapping_dims):
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


def existing_chart_ids(client, sheet_id):
    """Chart ids embedded on a given sheet, so re-runs can delete them first."""
    meta = client.get_spreadsheet()
    for s in meta.get("sheets", []):
        if s.get("properties", {}).get("sheetId") == sheet_id:
            return [c["chartId"] for c in s.get("charts", []) if "chartId" in c]
    return []


def grid_dv(sheet_id, r1, r2, c1, c2):
    return {
        "sheetId": sheet_id,
        "startRowIndex": r1,
        "endRowIndex": r2,
        "startColumnIndex": c1,
        "endColumnIndex": c2,
    }


def one_of_range(sheet_id, row0, col0, source):
    """A ONE_OF_RANGE dropdown on a single cell, sourced from an A1 range."""
    return {
        "setDataValidation": {
            "range": grid_dv(sheet_id, row0, row0 + 1, col0, col0 + 1),
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


def date_picker(sheet_id, row0, col0):
    """A DATE_IS_VALID rule on a single cell: Sheets shows a calendar picker."""
    return {
        "setDataValidation": {
            "range": grid_dv(sheet_id, row0, row0 + 1, col0, col0 + 1),
            "rule": {
                "condition": {"type": "DATE_IS_VALID"},
                "showCustomUi": True,
                "strict": False,
            },
        }
    }


def one_of_list(sheet_id, row0, col0, values):
    """A ONE_OF_LIST dropdown on a single cell, from a fixed list of values."""
    return {
        "setDataValidation": {
            "range": grid_dv(sheet_id, row0, row0 + 1, col0, col0 + 1),
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": v} for v in values],
                },
                "showCustomUi": True,
                "strict": False,
            },
        }
    }
