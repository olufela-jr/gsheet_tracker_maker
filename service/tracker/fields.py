"""Reading the Setup tab and Data Source headers, and validating them.

The Field tuple is the in-memory form of one Setup row; the *_of selectors
slice a field list by role so callers never re-implement the show/break-out
rules.
"""

from collections import namedtuple

from config import a1, sanitise_name

from .formulas import formula_tokens

# One declared field in the Setup tab.
#   name:    field name (Setup column A)
#   type:    "metric" | "dimension" | "date" | "calculated" (column B)
#   formula: bracket-token expression for a calculated field, or "" (C)
#   fmt:     "currency" | "percent" | "number" | "" number format hint (D)
#   show:    dimensions only — True shows the dimension as a filter in the
#            daily/weekly/monthly views; blank/False keeps it in the data but
#            hides it from the front end (Setup column E)
#   breakout: dimensions only — True gives the dimension its own break-out
#            table (totals per value) stacked on every view (Setup column F)
#   mapping: dimensions only — True lists the dimension's values in Mapping
#            even when it is neither shown nor broken out (Setup column G;
#            Show / Break-out imply a mapping column regardless)
Field = namedtuple(
    "Field",
    ["name", "type", "formula", "fmt", "show", "breakout", "mapping"],
    defaults=(False, False, False),
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
    dimension its own totals-per-value table on every view), G mapping
    (dimensions only — checked lists the values in Mapping even without
    Show / Break-out).
    """
    rows = client.read_range(a1(cfg.setup_tab, "A2:G"))
    fields = []
    for row in rows:
        if not row:
            continue
        name = _cell(row, 0)
        if not name:
            continue
        ftype = _cell(row, 1).lower()
        fields.append(
            Field(
                name=name,
                type=ftype,
                formula=_cell(row, 2),
                fmt=_cell(row, 3).lower(),
                show=_truthy(_cell(row, 4)),
                breakout=_truthy(_cell(row, 5)),
                mapping=_truthy(_cell(row, 6)),
            )
        )
    return fields


def is_calculated(field):
    return field.type == "calculated"


def field_key(name):
    """The identity of a field name, ignoring case and punctuation.

    Two names sharing a key are already rejected as duplicates, so a key
    identifies at most one field. That makes it safe for suggest_field_ to
    name the field a mistyped [Field] token probably meant.
    """
    return sanitise_name(name).lower()


def suggest_field_(token, fields):
    """A field whose name differs from `token` only in case or punctuation.

    Token matching itself stays exact; this only sharpens the error message.
    Returns None when nothing is close.
    """
    key = field_key(token)
    for f in fields:
        if field_key(f.name) == key:
            return f.name
    return None


def resolve_token_(index, token, fields):
    """Look up a [Field] token in a name -> value map, or raise ValidationError.

    build_views is a standalone Console action, so a formula that never went
    through validate reaches the builders; without this a mismatched token
    surfaces as a bare KeyError (a 500) instead of the guidance validate gives.
    """
    if token in index:
        return index[token]
    message = "Formula references unknown field '{}'.".format(token)
    near = suggest_field_(token, fields)
    if near:
        message += " Did you mean [{}]?".format(near)
    raise ValidationError([message])


def metric_fields_of(fields):
    """Metric and calculated fields, in Setup order (they render together)."""
    return [f for f in fields if f.type in ("metric", "calculated")]


def metrics_of(fields):
    return [f.name for f in metric_fields_of(fields)]


def dimensions_of(fields):
    """Dimension names shown as view slicers, in Setup order.

    Only dimensions with the Show box checked become filter dropdowns on the
    daily/weekly/monthly views. An unchecked dimension keeps its mapping
    column but gets no slicer, and metrics simply aggregate over all its
    values (no SUMIFS clause for it).
    """
    return [f.name for f in fields if f.type == "dimension" and f.show]


def breakout_dimensions_of(fields):
    """Dimension names that get their own break-out table, in Setup order.

    Independent of Show: a dimension can be a filter, a break-out table, both,
    or neither. A break-out table lists totals per value of the dimension.
    """
    return [f.name for f in fields if f.type == "dimension" and f.breakout]


def mapping_dimensions_of(fields):
    """Dimensions that get a Mapping column, in Setup order.

    Shown and broken-out dimensions always need one (their slicer dropdowns
    and break-out row labels source from it); the Mapping box adds a values
    column for a dimension that has neither, and leaving all three blank
    keeps a high-cardinality dimension out of the Mapping tab entirely.
    """
    return [
        f.name
        for f in fields
        if f.type == "dimension" and (f.show or f.breakout or f.mapping)
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

    Rules: at least one metric; exactly one date field; raw fields must be
    Data Source headers, while calculated fields skip that check but must
    carry a formula whose [Field] tokens reference known raw fields (a
    formula on any other type is an error); field
    names and Data Source headers must be unique once sanitised — the SUMIFS
    bind to one named range per sanitised name and Mapping reads data columns
    by header, so a duplicate silently points formulas at the wrong column.
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

    seen_fields = {}
    for f in fields:
        key = field_key(f.name)
        if key in seen_fields and f.name not in seen_fields[key]:
            errors.append(
                "Setup fields '{}' and '{}' collide (same name once sanitised); "
                "every field needs a unique name.".format(seen_fields[key][0], f.name)
            )
        elif key in seen_fields:
            errors.append(
                "Setup declares '{}' more than once; every field needs a "
                "unique name.".format(f.name)
            )
        seen_fields.setdefault(key, []).append(f.name)

    seen_headers = {}
    for header in headers:
        name = str(header).strip()
        if not name:
            continue
        key = field_key(name)
        if key in seen_headers and name not in seen_headers[key]:
            errors.append(
                "Data Source headers '{}' and '{}' collide (same name once "
                "sanitised); each column needs a unique header.".format(
                    seen_headers[key][0], name)
            )
        elif key in seen_headers:
            errors.append(
                "Data Source has duplicate header '{}'; each column needs a "
                "unique header.".format(name)
            )
        seen_headers.setdefault(key, []).append(name)

    valid_types = ("metric", "dimension", "date", "calculated")
    header_set = set(headers)
    for f in fields:
        if f.type not in valid_types:
            errors.append(
                "Setup field '{}' has unknown type '{}'; use one of {}."
                .format(f.name, f.type, ", ".join(valid_types))
            )
            continue
        if f.type == "calculated" and not f.formula:
            errors.append(
                "Calculated field '{}' has no formula; give it a [Field]-token "
                "expression like [Spend]/[Clicks].".format(f.name)
            )
        # A leading '=' makes Sheets treat the cell as a live formula, and
        # would otherwise reach the views as '=IFERROR(=B7/C7, "")'.
        if f.type == "calculated" and f.formula.startswith("="):
            errors.append(
                "Calculated field '{}' starts its formula with '='; drop it "
                "and write the expression alone, e.g. [Spend]/[Clicks]."
                .format(f.name)
            )
        # No tokens at all means the cell holds something that is not an
        # expression: a Sheets error value, or plain unbracketed names.
        if f.type == "calculated" and f.formula and not formula_tokens(f.formula):
            errors.append(
                "Calculated field '{}' has no [Field] tokens in '{}'; name the "
                "metrics in brackets, e.g. [Spend]/[Clicks]."
                .format(f.name, f.formula)
            )
        if f.type != "calculated" and f.formula:
            errors.append(
                "Field '{}' has a formula but type '{}'; only calculated "
                "fields take a formula.".format(f.name, f.type)
            )
        if is_calculated(f):
            for token in formula_tokens(f.formula):
                ref = by_name.get(token)
                if ref is None:
                    message = (
                        "Calculated field '{}' references unknown field '{}'."
                        .format(f.name, token)
                    )
                    near = suggest_field_(token, fields)
                    if near:
                        message += " Did you mean [{}]?".format(near)
                    errors.append(message)
                elif is_calculated(ref):
                    errors.append(
                        "Calculated field '{}' references another calculated "
                        "field '{}', which is not supported.".format(f.name, token)
                    )
                elif ref.type != "metric":
                    errors.append(
                        "Calculated field '{}' references '{}', which is not "
                        "a metric.".format(f.name, token)
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
