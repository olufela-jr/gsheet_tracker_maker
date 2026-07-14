"""Reading the Setup tab and Data Source headers, and validating them.

The Field tuple is the in-memory form of one Setup row; the *_of selectors
slice a field list by role so callers never re-implement the show/break-out
rules.
"""

from collections import namedtuple

from config import a1

from .formulas import formula_tokens

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


class ValidationError(Exception):
    """Raised when the Setup tab does not describe a usable tracker."""

    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(errors))


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
