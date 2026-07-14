"""The split-screen Comparison tab.

Two independent panels (Side A / Side B), each with a dropdown per shown
dimension and its own Date from / Date to, so you can compare specific
campaigns (or campaign types, regions, ...) over the same or different date
ranges. A metrics table shows each metric's total per side plus the %
difference. A metric picker and a day/week/month granularity picker drive a
trend chart that plots the chosen metric for both sides, aligned by period
index from each side's start date (so unequal date ranges still compare).

build_comparison orchestrates; each block on the tab (side panels, metrics
table, controls, trend helper) is laid out by its own _add_* function that
appends writes and formats to a shared Page.
"""

from collections import namedtuple

import theme
from config import column_to_letter, sanitise_name, a1

from .common import (
    Page,
    existing_chart_ids,
    grid_dv,
    one_of_list,
    one_of_range,
    read_date_serials,
)
from .fields import (
    ValidationError,
    date_field_of,
    dimensions_of,
    mapping_dimensions_of,
    read_data_source_headers,
    read_setup,
)
from .formulas import (
    DATE_FORMAT,
    DELTA_FORMAT,
    between_expr,
    between_formula,
    number_format_pattern,
)
from .scaffold import ensure_tab

# Rows in the Comparison tab's trend helper: the most periods a side's date
# range can chart (e.g. 52 weeks). Beyond this the trend line simply stops.
COMPARISON_PERIODS = 52

# Helper (chart-data) block far to the right: P index, per-side start/end,
# and the picked metric's value per side. Left visible; users rarely scroll.
_HP = 8  # column H (0-based 7) is the first helper column

# Everything build_comparison needs about the tracker, resolved once.
_Inputs = namedtuple(
    "_Inputs",
    [
        "cfg", "tab", "sentinel", "date_range",
        "metric_fields", "metric_names", "metrics_meta",
        "dimensions", "mapping_dims",
        "default_from", "default_to",
    ],
)

# The tab's fixed row plan, top to bottom. The chart overlays the trend title.
_Layout = namedtuple(
    "_Layout",
    [
        "header_row",       # "SIDE A" / "SIDE B"
        "first_dim_row",
        "from_row",
        "to_row",
        "read_header_row",  # Metric | Side A | Side B | % diff
        "read_first_row",
        "read_last_row",
        "controls_row",     # metric picker + granularity picker
        "trend_title_row",
        "hp_header_row",    # helper block header (top-aligned with the sides)
        "hp_first_row",
    ],
)

# The per-side SUMIFS specs and date cells the formula builders consume.
# *_rel use relative refs (B5); *_abs pin them ($B$5) for the helper block,
# whose formulas live in a different column and must not shift.
_Sides = namedtuple("_Sides", ["a_rel", "b_rel", "a_abs", "b_abs", "fa", "ta", "fb", "tb"])


def _comparison_inputs(client, cfg, fields, headers, serials):
    """Resolve setup, headers, and date defaults into an _Inputs bundle."""
    if fields is None:
        fields = read_setup(client, cfg)
    if headers is None:
        headers = read_data_source_headers(client, cfg)
    metric_fields = [f for f in fields if f.type == "metric"]
    date_name = date_field_of(fields)
    if date_name is None:
        raise ValidationError(
            ["No single date field is declared, so no comparison can be built."]
        )

    if serials is None:
        serials = read_date_serials(client, cfg, date_name, headers)
    numeric = [int(float(s)) for s in serials
               if isinstance(s, (int, float)) or str(s).replace(".", "", 1).isdigit()]

    return _Inputs(
        cfg=cfg,
        tab=cfg.comparison_tab,
        sentinel=cfg.sentinel,
        date_range=sanitise_name(date_name),
        metric_fields=metric_fields,
        metric_names=[m.name for m in metric_fields],
        metrics_meta=[(bool(m.formula), number_format_pattern(m.fmt))
                      for m in metric_fields],
        dimensions=dimensions_of(fields),
        mapping_dims=mapping_dimensions_of(fields),
        default_from=min(numeric) if numeric else "",
        default_to=max(numeric) if numeric else "",
    )


def _layout(v):
    """Compute the row plan from the dimension and metric counts."""
    header_row = 3
    first_dim_row = 4
    from_row = first_dim_row + len(v.dimensions)
    to_row = from_row + 1
    read_header_row = to_row + 2
    read_first_row = read_header_row + 1
    read_last_row = read_first_row + len(v.metric_fields) - 1
    controls_row = read_last_row + 2
    return _Layout(
        header_row=header_row,
        first_dim_row=first_dim_row,
        from_row=from_row,
        to_row=to_row,
        read_header_row=read_header_row,
        read_first_row=read_first_row,
        read_last_row=read_last_row,
        controls_row=controls_row,
        trend_title_row=controls_row + 2,
        hp_header_row=header_row,
        hp_first_row=header_row + 1,
    )


def _add_banner_and_side_headers(page, v, L):
    page.write("A1", [["{} - Comparison".format(v.cfg.frontend_title)]])
    page.write("A{}".format(L.header_row), [["SIDE A"]])
    page.write("D{}".format(L.header_row), [["SIDE B"]])
    page.fmt.append(theme.header_row(page.sheet_id, L.header_row - 1, 0, 2))
    page.fmt.append(theme.header_row(page.sheet_id, L.header_row - 1, 3, 5))


def _add_side_filters(page, v, L):
    """One dropdown per shown dimension plus Date from / Date to, per side."""
    specs_a_rel, specs_b_rel = [], []
    specs_a_abs, specs_b_abs = [], []
    for i, dim in enumerate(v.dimensions):
        r = L.first_dim_row + i
        page.write("A{}".format(r), [[dim, v.sentinel]])
        page.write("D{}".format(r), [[dim, v.sentinel]])
        rng = sanitise_name(dim)
        specs_a_rel.append((rng, "B{}".format(r)))
        specs_b_rel.append((rng, "E{}".format(r)))
        specs_a_abs.append((rng, "$B${}".format(r)))
        specs_b_abs.append((rng, "$E${}".format(r)))

    page.write("A{}".format(L.from_row), [["Date from", v.default_from]])
    page.write("D{}".format(L.from_row), [["Date from", v.default_from]])
    page.write("A{}".format(L.to_row), [["Date to", v.default_to]])
    page.write("D{}".format(L.to_row), [["Date to", v.default_to]])

    # Input cells (dimension dropdowns + the two date cells) on each side.
    page.fmt.append(theme.value_cells(page.sheet_id, L.first_dim_row - 1, L.to_row, 1, 2))
    page.fmt.append(theme.value_cells(page.sheet_id, L.first_dim_row - 1, L.to_row, 4, 5))
    page.fmt.append(theme.num_format(page.sheet_id, L.from_row - 1, L.to_row, 1, 2, DATE_FORMAT))
    page.fmt.append(theme.num_format(page.sheet_id, L.from_row - 1, L.to_row, 4, 5, DATE_FORMAT))
    page.fmt.append(theme.outer_border(page.sheet_id, L.header_row - 1, L.to_row, 0, 2))
    page.fmt.append(theme.outer_border(page.sheet_id, L.header_row - 1, L.to_row, 3, 5))

    return _Sides(
        a_rel=specs_a_rel, b_rel=specs_b_rel,
        a_abs=specs_a_abs, b_abs=specs_b_abs,
        fa="B{}".format(L.from_row), ta="B{}".format(L.to_row),
        fb="E{}".format(L.from_row), tb="E{}".format(L.to_row),
    )


def _add_metrics_table(page, v, L, sides):
    """Per-metric totals for each side plus the % difference between them."""
    page.write("A{}".format(L.read_header_row),
               [["Metric", "Side A", "Side B", "% diff"]])
    if v.metric_fields:
        page.write("A{}".format(L.read_first_row),
                   [[name] for name in v.metric_names])
        rows = []
        for i, m in enumerate(v.metric_fields):
            rr = L.read_first_row + i
            a_total = between_formula(
                m, v.date_range, '">="&{}'.format(sides.fa),
                '"<"&({}+1)'.format(sides.ta), sides.a_rel, v.sentinel)
            b_total = between_formula(
                m, v.date_range, '">="&{}'.format(sides.fb),
                '"<"&({}+1)'.format(sides.tb), sides.b_rel, v.sentinel)
            diff = '=IFERROR((C{r}-B{r})/B{r}, "")'.format(r=rr)
            rows.append([a_total, b_total, diff])
        page.write_formulas("B{}".format(L.read_first_row), rows)

    page.fmt.append(theme.header_row(page.sheet_id, L.read_header_row - 1, 0, 4))
    if v.metric_fields:
        page.fmt.append(theme.value_cells(
            page.sheet_id, L.read_first_row - 1, L.read_last_row, 0, 4))
        for i, (_is_calc, pattern) in enumerate(v.metrics_meta):
            rr = L.read_first_row - 1 + i
            page.fmt.append(theme.num_format(page.sheet_id, rr, rr + 1, 1, 3, pattern))
        page.fmt.append(theme.num_format(
            page.sheet_id, L.read_first_row - 1, L.read_last_row, 3, 4, DELTA_FORMAT))
        page.fmt.append(theme.outer_border(
            page.sheet_id, L.read_header_row - 1, L.read_last_row, 0, 4))


def _add_controls(page, v, L):
    """Metric picker + granularity picker, then the trend section title.

    Returns the absolute refs of the two picker cells for the helper formulas.
    """
    page.write("A{}".format(L.controls_row),
               [["Metric to chart",
                 v.metric_names[0] if v.metric_names else "",
                 "", "Granularity", "week"]])
    page.write("A{}".format(L.trend_title_row),
               [["Trend (aligned by period index from each start date)"]])

    page.fmt.append(theme.section_title(page.sheet_id, L.controls_row - 1, 5))
    page.fmt.append(theme.value_cells(page.sheet_id, L.controls_row - 1, L.controls_row, 1, 2))
    page.fmt.append(theme.value_cells(page.sheet_id, L.controls_row - 1, L.controls_row, 4, 5))
    page.fmt.append(theme.section_title(page.sheet_id, L.trend_title_row - 1, 8))

    return "$B${}".format(L.controls_row), "$E${}".format(L.controls_row)


def _add_trend_helper(page, v, L, mp, gp, sides):
    """The helper block that feeds the trend chart.

    Columns: H index, I/J side-A start/end, K side-A value, L/M side-B
    start/end, N side-B value. The value columns surface the picked metric.
    """
    page.write(
        "{}{}".format(column_to_letter(_HP), L.hp_header_row),
        [["P", "A start", "A end", "Side A", "B start", "B end", "Side B"]],
    )
    idx_col = column_to_letter(_HP)          # H
    a_start_col = column_to_letter(_HP + 1)  # I
    a_end_col = column_to_letter(_HP + 2)    # J
    b_start_col = column_to_letter(_HP + 4)  # L
    b_end_col = column_to_letter(_HP + 5)    # M

    # Helper header (navy) so the chart data reads as a labelled block. _HP is
    # a 1-based column number (H); grid indices are 0-based, so H is _HP - 1.
    page.fmt.append(theme.header_row(
        page.sheet_id, L.hp_header_row - 1, _HP - 1, _HP + 6))

    if not v.metric_names:
        return
    arr = "{" + ";".join('"{}"'.format(n) for n in v.metric_names) + "}"

    def start_formula(from_cell, r):
        return ('=IF({fa}="","",IF({g}="day",{fa}+({ix}{r}-1),'
                'IF({g}="week",{fa}+({ix}{r}-1)*7,EDATE({fa},{ix}{r}-1))))').format(
                    fa=from_cell, g=gp, ix=idx_col, r=r)

    def end_formula(start_ref, r):
        return ('=IF({s}="","",IF({g}="day",{s}+1,'
                'IF({g}="week",{s}+7,EDATE({s},1))))').format(
                    s="{}{}".format(start_ref, r), g=gp)

    def value_formula(start_ref, end_ref, to_cell, specs, r):
        lower = '">="&{}{}'.format(start_ref, r)
        upper = '"<"&{}{}'.format(end_ref, r)
        exprs = ",".join(
            between_expr(m, v.date_range, lower, upper, specs, v.sentinel)
            for m in v.metric_fields)
        return ('=IF(OR({s}{r}="",{s}{r}>{to}),"",'
                'CHOOSE(MATCH({mp},{arr},0),{exprs}))').format(
                    s=start_ref, r=r, to=to_cell, mp=mp, arr=arr, exprs=exprs)

    helper = []
    for k in range(COMPARISON_PERIODS):
        r = L.hp_first_row + k
        helper.append([
            k + 1,
            start_formula(sides.fa, r),
            end_formula(a_start_col, r),
            value_formula(a_start_col, a_end_col, sides.ta, sides.a_abs, r),
            start_formula(sides.fb, r),
            end_formula(b_start_col, r),
            value_formula(b_start_col, b_end_col, sides.tb, sides.b_abs, r),
        ])
    # The index column is a plain value; the rest are formulas. Writing the
    # whole block as USER_ENTERED lets the integers and formulas coexist.
    page.write_formulas("{}{}".format(idx_col, L.hp_first_row), helper)


def _add_dropdowns(page, v, L):
    """Dimension dropdowns per side, plus the metric and granularity pickers."""
    for i, dim in enumerate(v.dimensions):
        map_col = column_to_letter(v.mapping_dims.index(dim) + 1)
        source = "=" + a1(v.cfg.mapping_tab, "{c}2:{c}".format(c=map_col))
        r0 = L.first_dim_row - 1 + i
        page.validations.append(one_of_range(page.sheet_id, r0, 1, source))  # side A (B)
        page.validations.append(one_of_range(page.sheet_id, r0, 4, source))  # side B (E)
    if v.metric_names:
        page.validations.append(
            one_of_list(page.sheet_id, L.controls_row - 1, 1, v.metric_names))
    page.validations.append(
        one_of_list(page.sheet_id, L.controls_row - 1, 4, ["day", "week", "month"]))


def build_comparison(client, cfg, fields=None, headers=None, serials=None):
    """Build the split-screen Comparison tab (see module docstring).

    fields / headers / serials may be passed in when building several views in
    one run, so the inputs are read once, not per view (the read-quota matters).
    """
    v = _comparison_inputs(client, cfg, fields, headers, serials)
    L = _layout(v)

    ensure_tab(client, v.tab)
    sheet_id = client.get_sheet_id(v.tab)
    client.clear_range(v.tab)
    page = Page(v.tab, sheet_id)

    _add_banner_and_side_headers(page, v, L)
    sides = _add_side_filters(page, v, L)
    _add_metrics_table(page, v, L, sides)
    mp, gp = _add_controls(page, v, L)
    _add_trend_helper(page, v, L, mp, gp, sides)
    _add_dropdowns(page, v, L)

    page.flush_values(client)

    end_row = L.hp_first_row + COMPARISON_PERIODS + 2
    end_col = _HP + 7
    fmt = [
        theme.hide_gridlines(sheet_id),
        theme.canvas(sheet_id, end_row, end_col),
        theme.banner(sheet_id, 0, 5),
        theme.row_height(sheet_id, 0, 48),
        theme.col_width(sheet_id, 0, 1, 130),
        theme.col_width(sheet_id, 1, 2, 150),
        theme.col_width(sheet_id, 3, 4, 130),
        theme.col_width(sheet_id, 4, 5, 150),
    ]
    fmt.extend(page.fmt)
    # Clear the used area's validations, then add the dropdowns.
    fmt.append({"setDataValidation": {"range": grid_dv(sheet_id, 0, end_row, 0, end_col)}})
    fmt.extend(page.validations)
    for chart_id in existing_chart_ids(client, sheet_id):
        fmt.append({"deleteEmbeddedObject": {"objectId": chart_id}})

    client.batch_update(fmt)

    # Trend chart in its own batch (references the helper block just written).
    # 0-based grid columns: H (domain) is _HP-1, K (Side A value) _HP+2, N
    # (Side B value) _HP+5.
    if v.metric_names:
        header_idx = L.hp_header_row - 1
        end_idx = (L.hp_first_row - 1) + COMPARISON_PERIODS
        client.batch_update([
            theme.line_chart_request(
                sheet_id, [_HP + 2, _HP + 5], header_idx, end_idx, 0,
                title="Trend by period index",
                domain_col=_HP - 1, anchor_row=L.trend_title_row,
            )
        ])

    return {
        "tab": v.tab,
        "metrics": v.metric_names,
        "dimensions": v.dimensions,
        "periods": COMPARISON_PERIODS,
    }
