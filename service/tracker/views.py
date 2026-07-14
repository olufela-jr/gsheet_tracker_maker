"""The daily / weekly / monthly view tabs.

A view is a stack of blocks, top to bottom: a title banner; a filter bar (one
dropdown per shown dimension); a KPI strip of dimension-filtered grand totals;
on the weekly and monthly views a compare block (two period pickers with
per-metric A, B and % change); the by-period matrix (weekly/monthly also carry
a % change column beside each metric); then one break-out table per flagged
dimension (totals per value). The monthly view also gets a line chart.

build_view orchestrates; each block is laid out by its own _add_* function
that appends writes and formats to a shared Page. Positions come from the
Page's running row cursor, so blocks that vary in height (buckets, dimension
values) stack cleanly.
"""

from collections import namedtuple

import theme
from config import column_to_letter, sanitise_name, a1

from .common import (
    Page,
    existing_chart_ids,
    grid_dv,
    one_of_range,
    read_date_serials,
    read_mapping_values,
)
from .comparison import build_comparison
from .fields import (
    ValidationError,
    breakout_dimensions_of,
    date_field_of,
    dimensions_of,
    mapping_dimensions_of,
    read_data_source_headers,
    read_setup,
)
from .formulas import (
    DATE_FORMAT,
    DELTA_FORMAT,
    MONTH_FORMAT,
    breakout_formula,
    bucket_formula,
    distinct_buckets,
    grand_total_formula,
    number_format_pattern,
)
from .scaffold import ensure_tab

# Most values a single break-out table renders, so a high-cardinality dimension
# cannot stack thousands of rows. The cap is shown in the table title.
MAX_BREAKOUT_VALUES = 50

# Everything the block builders need about one view, resolved once.
#   metrics_meta:  (is_calculated, number_format_pattern) per metric
#   has_delta:     compare block + per-period % change columns; weekly and
#                  monthly only, and only when there is data to compare
#   mstep:         columns used per metric in the main matrix (2 with deltas)
#   kpi_last_col:  period + one column per metric
#   main_last_col: metric + its delta on wk/mo
_View = namedtuple(
    "_View",
    [
        "cfg", "tab", "granularity", "sentinel", "date_range",
        "metric_fields", "metric_names", "metrics_meta",
        "dimensions", "breakouts", "mapping_dims", "breakout_values",
        "buckets", "has_metrics", "has_buckets", "has_delta", "mstep",
        "date_pattern", "kpi_last_col", "main_last_col", "end_col",
    ],
)


def view_specs(cfg):
    """(tab, granularity) for the three view tabs, in display order."""
    return [
        (cfg.daily_tab, "day"),
        (cfg.weekly_tab, "week"),
        (cfg.monthly_tab, "month"),
    ]


def _view_inputs(client, cfg, tab, granularity, fields, headers, serials,
                 breakout_values):
    """Resolve setup, headers, buckets, and layout maths into a _View bundle."""
    if fields is None:
        fields = read_setup(client, cfg)
    if headers is None:
        headers = read_data_source_headers(client, cfg)
    metric_fields = [f for f in fields if f.type == "metric"]
    dimensions = dimensions_of(fields)
    breakouts = breakout_dimensions_of(fields)
    mapping_dims = mapping_dimensions_of(fields)
    date_name = date_field_of(fields)
    if date_name is None:
        raise ValidationError(
            ["No single date field is declared, so no view can be built."]
        )

    if serials is None:
        serials = read_date_serials(client, cfg, date_name, headers)
    buckets = distinct_buckets(serials, granularity)

    num_metrics = len(metric_fields)
    has_metrics = num_metrics > 0
    has_buckets = bool(buckets)
    # Compare + per-period deltas live on weekly and monthly, not daily.
    has_delta = granularity in ("week", "month") and has_buckets and has_metrics
    mstep = 2 if has_delta else 1

    if breakouts and breakout_values is None:
        breakout_values = read_mapping_values(client, cfg, mapping_dims)

    kpi_last_col = 1 + num_metrics
    main_last_col = 1 + num_metrics * mstep
    return _View(
        cfg=cfg,
        tab=tab,
        granularity=granularity,
        sentinel=cfg.sentinel,
        date_range=sanitise_name(date_name),
        metric_fields=metric_fields,
        metric_names=[m.name for m in metric_fields],
        metrics_meta=[(bool(m.formula), number_format_pattern(m.fmt))
                      for m in metric_fields],
        dimensions=dimensions,
        breakouts=breakouts,
        mapping_dims=mapping_dims,
        breakout_values=breakout_values or {},
        buckets=buckets,
        has_metrics=has_metrics,
        has_buckets=has_buckets,
        has_delta=has_delta,
        mstep=mstep,
        date_pattern=MONTH_FORMAT if granularity == "month" else DATE_FORMAT,
        kpi_last_col=kpi_last_col,
        main_last_col=main_last_col,
        end_col=max(main_last_col, kpi_last_col, len(dimensions), 4, 8),
    )


def _add_title(page, v):
    title = "{} - {}".format(v.cfg.frontend_title, v.granularity.capitalize())
    page.write("A1", [[title]])
    page.row = 3  # leave row 2 blank


def _add_filter_bar(page, v):
    """Label row + dropdown row, one per shown dimension.

    Returns (dim_specs, drop_row): the (named_range, dropdown_cell) pair per
    dimension that every SUMIFS filters by, and the dropdown row for the
    validation wiring. ([], None) when no dimension is shown.
    """
    if not v.dimensions:
        return [], None
    label_row = page.row
    drop_row = page.row + 1
    page.write("A{}".format(label_row), [list(v.dimensions)])
    page.write("A{}".format(drop_row), [[v.sentinel for _ in v.dimensions]])
    dim_specs = [
        (sanitise_name(dim), "{}{}".format(column_to_letter(i + 1), drop_row))
        for i, dim in enumerate(v.dimensions)
    ]

    ndim = len(v.dimensions)
    page.fmt.append(theme.header_row(page.sheet_id, label_row - 1, 0, ndim))
    page.fmt.append(theme.value_cells(page.sheet_id, drop_row - 1, drop_row, 0, ndim))
    page.fmt.append(theme.outer_border(page.sheet_id, label_row - 1, drop_row, 0, ndim))

    page.row = drop_row + 2
    return dim_specs, drop_row


def _add_kpi_strip(page, v, dim_specs):
    """Dimension-filtered grand totals, one per metric (no date bucket)."""
    if not v.has_metrics:
        return
    label_row = page.row
    value_row = page.row + 1
    page.write("A{}".format(label_row), [["Totals"] + v.metric_names])
    page.write_formulas(
        "B{}".format(value_row),
        [[grand_total_formula(m, dim_specs, v.sentinel) for m in v.metric_fields]],
    )

    page.fmt.append(theme.header_row(page.sheet_id, label_row - 1, 0, v.kpi_last_col))
    page.fmt.append(theme.value_cells(page.sheet_id, value_row - 1, value_row, 0, v.kpi_last_col))
    page.fmt.append(theme.kpi_values(page.sheet_id, value_row - 1, 1, v.kpi_last_col))
    for i, (_is_calc, pattern) in enumerate(v.metrics_meta):
        page.fmt.append(theme.num_format(
            page.sheet_id, value_row - 1, value_row, 1 + i, 2 + i, pattern))
    page.fmt.append(theme.outer_border(page.sheet_id, label_row - 1, value_row, 0, v.kpi_last_col))

    page.row = value_row + 2


def _add_compare_block(page, v, dim_specs):
    """Two period pickers with per-metric A, B and % change (weekly/monthly).

    Returns the picker row so the dropdown wiring can point the pickers at the
    period column of the matrix laid out below; None when the block is absent.
    """
    if not v.has_delta:
        return None
    picker_row = page.row
    header_row = page.row + 1
    first_row = page.row + 2
    pa = "B{}".format(picker_row)
    pb = "C{}".format(picker_row)
    page.write("A{}".format(picker_row), [["Compare"]])
    page.write("B{r}:C{r}".format(r=picker_row), [[v.buckets[0], v.buckets[-1]]])
    page.write("A{}".format(header_row),
               [["Metric", "Period A", "Period B", "Change"]])
    page.write("A{}".format(first_row), [[name] for name in v.metric_names])
    rows = []
    for i, m in enumerate(v.metric_fields):
        r = first_row + i
        a_formula = bucket_formula(m, v.date_range, pa, v.granularity, dim_specs, v.sentinel)
        b_formula = bucket_formula(m, v.date_range, pb, v.granularity, dim_specs, v.sentinel)
        change = '=IFERROR((C{r}-B{r})/B{r}, "")'.format(r=r)
        rows.append([a_formula, b_formula, change])
    page.write_formulas("B{}".format(first_row), rows)

    last_col = 4  # Metric | Period A | Period B | Change
    end = first_row - 1 + len(v.metric_fields)
    page.fmt.append(theme.section_title(page.sheet_id, picker_row - 1, 1))
    page.fmt.append(theme.value_cells(page.sheet_id, picker_row - 1, picker_row, 1, 3))
    page.fmt.append(theme.num_format(
        page.sheet_id, picker_row - 1, picker_row, 1, 3, v.date_pattern))
    page.fmt.append(theme.header_row(page.sheet_id, header_row - 1, 0, last_col))
    page.fmt.append(theme.value_cells(page.sheet_id, first_row - 1, end, 0, last_col))
    for i, (_is_calc, pattern) in enumerate(v.metrics_meta):
        rr = first_row - 1 + i
        page.fmt.append(theme.num_format(page.sheet_id, rr, rr + 1, 1, 3, pattern))
    page.fmt.append(theme.num_format(page.sheet_id, first_row - 1, end, 3, 4, DELTA_FORMAT))
    page.fmt.append(theme.outer_border(page.sheet_id, header_row - 1, end, 0, last_col))

    page.row = first_row + len(v.metric_fields) + 2
    return picker_row


def _add_period_matrix(page, v, dim_specs):
    """One row per date bucket, one column (plus optional delta) per metric.

    Returns (header_row, first_data_row) for the chart and the compare-picker
    dropdowns; None when there are no metrics.
    """
    if not v.has_metrics:
        return None
    title_row = page.row
    header_row = page.row + 1
    first_data = page.row + 2
    page.write("A{}".format(title_row), [["By {}".format(v.granularity)]])
    header = ["Period"]
    for name in v.metric_names:
        header.append(name)
        if v.has_delta:
            header.append("change %")
    page.write("A{}".format(header_row), [header])
    if v.has_buckets:
        page.write("A{}".format(first_data), [[b] for b in v.buckets])
        matrix = []
        for j in range(len(v.buckets)):
            prow = first_data + j
            cell = "A{}".format(prow)
            line = []
            for i, m in enumerate(v.metric_fields):
                line.append(bucket_formula(m, v.date_range, cell, v.granularity,
                                           dim_specs, v.sentinel))
                if v.has_delta:
                    vc = column_to_letter(2 + i * v.mstep)  # value column letter
                    if j == 0:
                        line.append("")
                    else:
                        line.append('=IFERROR(({vc}{r}-{vc}{p})/{vc}{p}, "")'.format(
                            vc=vc, r=prow, p=prow - 1))
            matrix.append(line)
        page.write_formulas("B{}".format(first_data), matrix)

    page.fmt.append(theme.section_title(page.sheet_id, title_row - 1, v.main_last_col))
    page.fmt.append(theme.header_row(page.sheet_id, header_row - 1, 0, v.main_last_col))
    if v.has_buckets:
        end = first_data - 1 + len(v.buckets)
        page.fmt.append(theme.value_cells(page.sheet_id, first_data - 1, end, 0, v.main_last_col))
        page.fmt.append(theme.num_format(page.sheet_id, first_data - 1, end, 0, 1, v.date_pattern))
        for i, (is_calc, pattern) in enumerate(v.metrics_meta):
            vcol = 1 + i * v.mstep
            page.fmt.append(theme.num_format(
                page.sheet_id, first_data - 1, end, vcol, vcol + 1, pattern))
            if is_calc:
                page.fmt.append(theme.periwinkle_col(page.sheet_id, first_data - 1, end, vcol))
            if v.has_delta:
                page.fmt.append(theme.num_format(
                    page.sheet_id, first_data - 1, end, vcol + 1, vcol + 2, DELTA_FORMAT))
        page.fmt.append(theme.outer_border(page.sheet_id, header_row - 1, end, 0, v.main_last_col))

    page.row = first_data + len(v.buckets) + 2
    return header_row, first_data


def _add_breakout_tables(page, v, dim_specs):
    """One totals-per-value table per broken-out dimension, stacked in order."""
    for bd in v.breakouts:
        all_vals = v.breakout_values.get(bd, [])
        # Cap high-cardinality dimensions so one break-out cannot push a table
        # thousands of rows tall; the cap is surfaced, never silent.
        vals = all_vals[:MAX_BREAKOUT_VALUES]
        truncated = len(all_vals) > MAX_BREAKOUT_VALUES
        bd_range = sanitise_name(bd)
        other_specs = [spec for dim, spec in zip(v.dimensions, dim_specs) if dim != bd]
        title = "By {}".format(bd)
        if truncated:
            title += "  (first {} of {})".format(MAX_BREAKOUT_VALUES, len(all_vals))
        title_row = page.row
        header_row = page.row + 1
        first_data = page.row + 2
        page.write("A{}".format(title_row), [[title]])
        page.write("A{}".format(header_row), [[bd] + v.metric_names])
        if vals:
            page.write("A{}".format(first_data), [[val] for val in vals])
            block = []
            for k, val_ in enumerate(vals):
                vcell = "A{}".format(first_data + k)
                block.append([breakout_formula(m, bd_range, vcell, other_specs, v.sentinel)
                              for m in v.metric_fields])
            page.write_formulas("B{}".format(first_data), block)

        page.fmt.append(theme.section_title(page.sheet_id, title_row - 1, v.kpi_last_col))
        page.fmt.append(theme.header_row(page.sheet_id, header_row - 1, 0, v.kpi_last_col))
        if vals:
            end = first_data - 1 + len(vals)
            page.fmt.append(theme.value_cells(
                page.sheet_id, first_data - 1, end, 0, v.kpi_last_col))
            for i, (is_calc, pattern) in enumerate(v.metrics_meta):
                mcol = 1 + i
                page.fmt.append(theme.num_format(
                    page.sheet_id, first_data - 1, end, mcol, mcol + 1, pattern))
                if is_calc:
                    page.fmt.append(theme.periwinkle_col(page.sheet_id, first_data - 1, end, mcol))
            page.fmt.append(theme.outer_border(
                page.sheet_id, header_row - 1, end, 0, v.kpi_last_col))

        page.row = first_data + len(vals) + 2


def _add_dropdowns(page, v, drop_row, picker_row, matrix):
    """Filter-bar dropdowns from Mapping; compare pickers from the matrix."""
    for i, dim in enumerate(v.dimensions):
        map_col = column_to_letter(v.mapping_dims.index(dim) + 1)
        source = "=" + a1(v.cfg.mapping_tab, "{c}2:{c}".format(c=map_col))
        page.validations.append(one_of_range(page.sheet_id, drop_row - 1, i, source))
    if picker_row and matrix and v.has_buckets:
        _mh, first_data = matrix
        period_src = "=" + a1(v.tab, "A{r1}:A{r2}".format(
            r1=first_data, r2=first_data + len(v.buckets) - 1))
        page.validations.append(one_of_range(page.sheet_id, picker_row - 1, 1, period_src))  # Period A
        page.validations.append(one_of_range(page.sheet_id, picker_row - 1, 2, period_src))  # Period B


def build_view(client, cfg, tab, granularity, fields=None, headers=None,
               serials=None, breakout_values=None):
    """Build one themed view tab as a stack of blocks (see module docstring).

    fields / headers / serials / breakout_values may be passed in when building
    several views in one run, so the inputs are read once, not per view (the
    read-quota matters).
    """
    v = _view_inputs(client, cfg, tab, granularity, fields, headers, serials,
                     breakout_values)

    ensure_tab(client, tab)
    sheet_id = client.get_sheet_id(tab)
    client.clear_range(tab)
    page = Page(tab, sheet_id)

    _add_title(page, v)
    dim_specs, drop_row = _add_filter_bar(page, v)
    _add_kpi_strip(page, v, dim_specs)
    picker_row = _add_compare_block(page, v, dim_specs)
    matrix = _add_period_matrix(page, v, dim_specs)
    _add_breakout_tables(page, v, dim_specs)
    _add_dropdowns(page, v, drop_row, picker_row, matrix)

    end_row = page.row
    page.flush_values(client)

    fmt = [
        theme.hide_gridlines(sheet_id),
        theme.canvas(sheet_id, end_row + 1, v.end_col),
        theme.banner(sheet_id, 0, v.end_col),
        theme.row_height(sheet_id, 0, 48),
        theme.col_width(sheet_id, 0, 1, 140),
        theme.col_width(sheet_id, 1, v.end_col, 110),
    ]
    fmt.extend(page.fmt)
    # Clear the used area's validations, then add the dropdowns.
    fmt.append({"setDataValidation": {"range": grid_dv(sheet_id, 0, end_row, 0, 60)}})
    fmt.extend(page.validations)
    if granularity == "month":
        for chart_id in existing_chart_ids(client, sheet_id):
            fmt.append({"deleteEmbeddedObject": {"objectId": chart_id}})

    client.batch_update(fmt)

    # The chart is added in its own batch: it references the matrix the prior
    # writes created, and delete-then-add in one batch can race.
    if granularity == "month" and matrix and v.has_buckets and v.has_metrics:
        header_row, first_data = matrix
        metric_value_cols = [1 + i * v.mstep for i in range(len(v.metric_fields))]
        client.batch_update([
            theme.line_chart_request(
                sheet_id, metric_value_cols, header_row - 1,
                (first_data - 1) + len(v.buckets), v.main_last_col + 1,
            )
        ])

    return {
        "tab": tab,
        "granularity": granularity,
        "metrics": v.metric_names,
        "dimensions": v.dimensions,
        "breakouts": v.breakouts,
        "buckets": len(v.buckets),
    }


def build_views(client, cfg):
    """Build the three period views plus the Comparison tab.

    Setup fields, data_source headers, the date column, and (when any dimension
    is broken out) the Mapping values are read once here and passed to each
    view, so building everything stays a few reads, not several per tab.
    """
    fields = read_setup(client, cfg)
    headers = read_data_source_headers(client, cfg)
    date_name = date_field_of(fields)
    serials = (
        read_date_serials(client, cfg, date_name, headers) if date_name else []
    )
    breakout_values = (
        read_mapping_values(client, cfg, mapping_dimensions_of(fields))
        if breakout_dimensions_of(fields)
        else {}
    )
    results = [
        build_view(client, cfg, tab, granularity, fields, headers, serials, breakout_values)
        for tab, granularity in view_specs(cfg)
    ]
    results.append(build_comparison(client, cfg, fields, headers, serials))
    return results
