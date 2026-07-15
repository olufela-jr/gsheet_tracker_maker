"""The daily / weekly / monthly view tabs.

A view is a stack of blocks, top to bottom: a title banner; the header (a
date-controls row, then the slicer pairs — dimension | dropdown, four to a
row — with live Today and days-left-in-month stat cells at the right); on
the weekly and monthly views the comparison block (two From/To date ranges
side by side with the per-metric totals for each and a % change row
underneath, filtered by the slicers); a KPI strip of slicer-filtered grand
totals; the by-period block; then one break-out block per flagged dimension
(totals per value). The monthly view also gets a line chart.

The matrix's period column is a formula-driven window scoped by the tab's
date controls. Daily's Date from / Date to are dropdowns of the available
dates (the Mapping tab's date column), blank by default — the by-day block
then shows the newest 14 days of data. Weekly's are calendar pickers
defaulting to the last 28 days (up to 6 Monday-start weeks, newest first).
Monthly has a Year dropdown (defaulting to the current calendar year)
and runs January down to December, with months past TODAY() blank. Rows past the
picked range blank out, so changing the pickers re-scopes every matrix
without a rebuild.

build_view orchestrates; each block is laid out by its own _add_* function
that appends writes and formats to a shared Page. Positions come from the
Page's running row cursor, so blocks that vary in height (period windows,
dimension values) stack cleanly.
"""

from collections import namedtuple

import theme
from config import column_to_letter, sanitise_name, a1

from .common import (
    Page,
    date_picker,
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
    PERIOD_ROWS,
    between_formula,
    blank_guarded,
    breakout_formula,
    bucket_formula,
    grand_total_formula,
    number_format_pattern,
    period_next_formula,
    period_start_formula,
    picker_default_formulas,
    range_guarded,
)
from .scaffold import ensure_tab

# Most values a single break-out table renders, so a high-cardinality dimension
# cannot stack thousands of rows. The cap is shown in the table title.
MAX_BREAKOUT_VALUES = 50

# Everything the block builders need about one view, resolved once.
#   metrics_meta:  (is_calculated, number_format_pattern) per metric
#   num_periods:   rows in the period matrix (the PERIOD_ROWS window)
#   has_compare:   the From/To compare table; weekly and monthly only
#   kpi_last_col:  label/period column + one column per metric (also the
#                  matrix width)
_View = namedtuple(
    "_View",
    [
        "cfg", "tab", "granularity", "sentinel", "date_range",
        "dates_src", "years_src",
        "metric_fields", "metric_names", "metrics_meta",
        "dimensions", "breakouts", "mapping_dims", "breakout_values",
        "num_periods", "has_metrics", "has_compare",
        "date_pattern", "kpi_last_col", "end_col",
    ],
)


def view_specs(cfg):
    """(tab, granularity) for the three view tabs, in display order."""
    return [
        (cfg.daily_tab, "day"),
        (cfg.weekly_tab, "week"),
        (cfg.monthly_tab, "month"),
    ]


def _view_inputs(client, cfg, tab, granularity, fields, headers,
                 breakout_values):
    """Resolve setup, headers, and layout maths into a _View bundle."""
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

    num_metrics = len(metric_fields)
    has_metrics = num_metrics > 0
    # The compare table lives on weekly and monthly, not daily.
    has_compare = granularity in ("week", "month") and has_metrics

    if breakouts and breakout_values is None:
        breakout_values = read_mapping_values(client, cfg, mapping_dims)

    kpi_last_col = 1 + num_metrics
    # From | To | one column per metric.
    compare_last_col = 2 + num_metrics if has_compare else 0
    # The Mapping tab's available-dates and available-years columns (after
    # the dimension columns); the date and Year dropdowns and the daily
    # fallback window source from them.
    dates_col = column_to_letter(len(mapping_dims) + 1)
    years_col = column_to_letter(len(mapping_dims) + 2)
    dates_src = a1(cfg.mapping_tab, "{c}2:{c}".format(c=dates_col))
    years_src = a1(cfg.mapping_tab, "{c}2:{c}".format(c=years_col))
    return _View(
        cfg=cfg,
        tab=tab,
        granularity=granularity,
        sentinel=cfg.sentinel,
        date_range=sanitise_name(date_name),
        dates_src=dates_src,
        years_src=years_src,
        metric_fields=metric_fields,
        metric_names=[m.name for m in metric_fields],
        metrics_meta=[(bool(m.formula), number_format_pattern(m.fmt))
                      for m in metric_fields],
        dimensions=dimensions,
        breakouts=breakouts,
        mapping_dims=mapping_dims,
        breakout_values=breakout_values or {},
        num_periods=PERIOD_ROWS[granularity],
        has_metrics=has_metrics,
        has_compare=has_compare,
        date_pattern=MONTH_FORMAT if granularity == "month" else DATE_FORMAT,
        kpi_last_col=kpi_last_col,
        # Wide enough for the header's stat cells (columns I:J).
        end_col=max(kpi_last_col, compare_last_col, _STAT_COL + 2),
    )


def _add_title(page, v):
    title = "{} - {}".format(v.cfg.frontend_title, v.granularity.capitalize())
    page.write("A1", [[title]])
    page.row = 3  # leave row 2 blank


# Dimension | dropdown pairs per control-header row (the dummy dashboard's
# compact layout: label left, value right, four pairs across).
PAIRS_PER_ROW = 4

# 0-based column of the live stat labels, right of the widest pairs grid.
_STAT_COL = PAIRS_PER_ROW * 2


def _add_filter_header(page, v):
    """The header: date controls, slicer pairs grid, and live stat cells.

    The first row holds the tab's date controls as label | value pairs —
    Date from / Date to calendar pickers with rolling-window defaults, or the
    monthly view's Year dropdown. Each shown dimension renders below
    as a name | sentinel-dropdown pair, packed PAIRS_PER_ROW to a grid row.
    The Today and days-left-in-month stats sit to the right at a fixed
    column (the days-left label is itself a live formula).

    Returns (dim_specs, drop_positions, pickers): the
    (named_range, dropdown_cell) pair per dimension that every SUMIFS
    filters by, each dropdown's 0-based (row, col) for the validation
    wiring, and the date-control ref(s) the period matrix anchors on — a
    (from, to) pair, or the year cell.
    """
    first_row = page.row
    dates_row = first_row
    if v.granularity == "month":
        page.write_formulas(
            "A{}".format(dates_row),
            [["Year", picker_default_formulas("month")]],
        )
        page.validations.append(one_of_range(
            page.sheet_id, dates_row - 1, 1, "=" + v.years_src))
        page.fmt.append(theme.num_format(
            page.sheet_id, dates_row - 1, dates_row, 1, 2, "0"))
        date_pairs = 1
        pickers = "$B${}".format(dates_row)
    else:
        if v.granularity == "day":
            # No defaults: blank dropdowns of the available dates; the by-day
            # block falls back to the newest data while they stay blank.
            page.write("A{}".format(dates_row),
                       [["Date from", "", "Date to", ""]])
            src = "=" + v.dates_src
            page.validations.append(
                one_of_range(page.sheet_id, dates_row - 1, 1, src))
            page.validations.append(
                one_of_range(page.sheet_id, dates_row - 1, 3, src))
        else:
            d_from, d_to = picker_default_formulas(v.granularity)
            page.write_formulas("A{}".format(dates_row),
                                [["Date from", d_from, "Date to", d_to]])
            page.validations.append(date_picker(page.sheet_id, dates_row - 1, 1))
            page.validations.append(date_picker(page.sheet_id, dates_row - 1, 3))
        page.fmt.append(theme.num_format(
            page.sheet_id, dates_row - 1, dates_row, 1, 2, DATE_FORMAT))
        page.fmt.append(theme.num_format(
            page.sheet_id, dates_row - 1, dates_row, 3, 4, DATE_FORMAT))
        date_pairs = 2
        pickers = ("$B${}".format(dates_row), "$D${}".format(dates_row))
    for k in range(date_pairs):
        page.fmt.append(theme.header_row(
            page.sheet_id, dates_row - 1, k * 2, k * 2 + 1))
        page.fmt.append(theme.value_cells(
            page.sheet_id, dates_row - 1, dates_row, k * 2 + 1, k * 2 + 2))
    page.fmt.append(theme.outer_border(
        page.sheet_id, dates_row - 1, dates_row, 0, 2 * date_pairs))

    # Slicer pairs grid, below the date controls.
    grid_first = first_row + 1
    dim_specs, drop_positions = [], []
    for i, dim in enumerate(v.dimensions):
        r = grid_first + i // PAIRS_PER_ROW
        col0 = (i % PAIRS_PER_ROW) * 2  # 0-based label column
        drop_positions.append((r - 1, col0 + 1))
        dim_specs.append(
            (sanitise_name(dim), "{}{}".format(column_to_letter(col0 + 2), r))
        )
    grid_rows = -(-len(v.dimensions) // PAIRS_PER_ROW)  # ceil
    for gr in range(grid_rows):
        row_dims = v.dimensions[gr * PAIRS_PER_ROW:(gr + 1) * PAIRS_PER_ROW]
        line = []
        for dim in row_dims:
            line.extend([dim, v.sentinel])
        page.write("A{}".format(grid_first + gr), [line])
    for r0, c0 in drop_positions:
        page.fmt.append(theme.header_row(page.sheet_id, r0, c0 - 1, c0))
        page.fmt.append(theme.value_cells(page.sheet_id, r0, r0 + 1, c0, c0 + 1))
    if v.dimensions:
        page.fmt.append(theme.outer_border(
            page.sheet_id, grid_first - 1, grid_first - 1 + grid_rows,
            0, 2 * min(len(v.dimensions), PAIRS_PER_ROW)))

    stat_ref = "{}{}".format(column_to_letter(_STAT_COL + 1), first_row)
    page.write_formulas(stat_ref, [
        ["Today", "=TODAY()"],
        ['="Days Left in "&TEXT(TODAY(),"mmmm")', "=EOMONTH(TODAY(),0)-TODAY()+1"],
    ])
    page.fmt.append(theme.header_row(
        page.sheet_id, first_row - 1, _STAT_COL, _STAT_COL + 1))
    page.fmt.append(theme.header_row(
        page.sheet_id, first_row, _STAT_COL, _STAT_COL + 1))
    page.fmt.append(theme.value_cells(
        page.sheet_id, first_row - 1, first_row + 1, _STAT_COL + 1, _STAT_COL + 2))
    page.fmt.append(theme.num_format(
        page.sheet_id, first_row - 1, first_row, _STAT_COL + 1, _STAT_COL + 2,
        DATE_FORMAT))
    page.fmt.append(theme.num_format(
        page.sheet_id, first_row, first_row + 1, _STAT_COL + 1, _STAT_COL + 2, "0"))
    page.fmt.append(theme.outer_border(
        page.sheet_id, first_row - 1, first_row + 1, _STAT_COL, _STAT_COL + 2))

    page.row = first_row + max(1 + grid_rows, 2) + 1
    return dim_specs, drop_positions, pickers


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
    """Two From/To date ranges side by side, with % change underneath.

    Sits just below the header; the totals are filtered by the slicer
    dropdowns above (dim_specs), so one slice is compared across the two
    ranges. The date cells are dropdowns of the available dates (from the
    Mapping date column), blank by default — a row's totals and the % change
    fill in once both of its dates are picked. Weekly and monthly only.
    """
    if not v.has_compare:
        return
    header_row = page.row
    a_row = page.row + 1
    b_row = page.row + 2
    diff_row = page.row + 3
    page.write("A{}".format(header_row), [["From", "To"] + v.metric_names])

    def totals(row):
        lower = '">="&$A{}'.format(row)
        upper = '"<"&($B{}+1)'.format(row)
        return [
            range_guarded(
                between_formula(m, v.date_range, lower, upper, dim_specs, v.sentinel),
                "$A{}".format(row), "$B{}".format(row),
            )
            for m in v.metric_fields
        ]

    diffs = [
        '=IFERROR(({c}{b}-{c}{a})/{c}{a}, "")'.format(
            c=column_to_letter(3 + i), b=b_row, a=a_row)
        for i in range(len(v.metric_fields))
    ]
    page.write_formulas("A{}".format(a_row), [
        ["", ""] + totals(a_row),
        ["", ""] + totals(b_row),
        ["% change", ""] + diffs,
    ])

    last_col = 2 + len(v.metric_fields)
    page.fmt.append(theme.header_row(page.sheet_id, header_row - 1, 0, last_col))
    page.fmt.append(theme.value_cells(page.sheet_id, a_row - 1, diff_row, 0, last_col))
    page.fmt.append(theme.num_format(
        page.sheet_id, a_row - 1, b_row, 0, 2, DATE_FORMAT))
    for i, (_is_calc, pattern) in enumerate(v.metrics_meta):
        page.fmt.append(theme.num_format(
            page.sheet_id, a_row - 1, b_row, 2 + i, 3 + i, pattern))
    page.fmt.append(theme.highlight_cells(
        page.sheet_id, diff_row - 1, diff_row, 0, last_col))
    page.fmt.append(theme.num_format(
        page.sheet_id, diff_row - 1, diff_row, 2, last_col, DELTA_FORMAT))
    page.fmt.append(theme.outer_border(
        page.sheet_id, header_row - 1, diff_row, 0, last_col))
    src = "=" + v.dates_src
    for r0 in (a_row - 1, b_row - 1):
        page.validations.append(one_of_range(page.sheet_id, r0, 0, src))
        page.validations.append(one_of_range(page.sheet_id, r0, 1, src))

    page.row = diff_row + 2


def _add_period_matrix(page, v, dim_specs, pickers):
    """One row per window period, one column per metric.

    The period column is formulas: the first cell anchors the window on the
    tab's date controls and each row below derives from the one above, going
    blank past the picked range. Daily and weekly run newest first; monthly
    runs the picked year January downwards (so the chart reads chronologically)
    with unreached months blank.

    Returns (header_row, first_data_row) for the chart and the compare-picker
    dropdowns; None when there are no metrics.
    """
    if not v.has_metrics:
        return None
    title_row = page.row
    header_row = page.row + 1
    first_data = page.row + 2
    page.write("A{}".format(title_row), [["By {}".format(v.granularity)]])
    page.write("A{}".format(header_row), [["Period"] + v.metric_names])

    periods = [[period_start_formula(v.granularity, pickers, v.dates_src)]]
    first_cell = "$A${}".format(first_data)
    for j in range(1, v.num_periods):
        periods.append(
            [period_next_formula(v.granularity, "A{}".format(first_data + j - 1),
                                 pickers, first_cell)]
        )
    page.write_formulas("A{}".format(first_data), periods)

    matrix = []
    for j in range(v.num_periods):
        cell = "A{}".format(first_data + j)
        matrix.append([
            blank_guarded(
                bucket_formula(m, v.date_range, cell, v.granularity,
                               dim_specs, v.sentinel),
                cell,
            )
            for m in v.metric_fields
        ])
    page.write_formulas("B{}".format(first_data), matrix)

    page.fmt.append(theme.section_title(page.sheet_id, title_row - 1, v.kpi_last_col))
    page.fmt.append(theme.header_row(page.sheet_id, header_row - 1, 0, v.kpi_last_col))
    end = first_data - 1 + v.num_periods
    page.fmt.append(theme.value_cells(page.sheet_id, first_data - 1, end, 0, v.kpi_last_col))
    page.fmt.append(theme.num_format(page.sheet_id, first_data - 1, end, 0, 1, v.date_pattern))
    for i, (is_calc, pattern) in enumerate(v.metrics_meta):
        page.fmt.append(theme.num_format(
            page.sheet_id, first_data - 1, end, 1 + i, 2 + i, pattern))
        if is_calc:
            page.fmt.append(theme.highlight_col(page.sheet_id, first_data - 1, end, 1 + i))
    page.fmt.append(theme.outer_border(page.sheet_id, header_row - 1, end, 0, v.kpi_last_col))

    page.row = first_data + v.num_periods + 2
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
                    page.fmt.append(theme.highlight_col(page.sheet_id, first_data - 1, end, mcol))
            page.fmt.append(theme.outer_border(
                page.sheet_id, header_row - 1, end, 0, v.kpi_last_col))

        page.row = first_data + len(vals) + 2


def _add_dropdowns(page, v, drop_positions):
    """Slicer dropdowns, sourced from each dimension's Mapping column."""
    for (r0, c0), dim in zip(drop_positions, v.dimensions):
        map_col = column_to_letter(v.mapping_dims.index(dim) + 1)
        source = "=" + a1(v.cfg.mapping_tab, "{c}2:{c}".format(c=map_col))
        page.validations.append(one_of_range(page.sheet_id, r0, c0, source))


def build_view(client, cfg, tab, granularity, fields=None, headers=None,
               breakout_values=None):
    """Build one themed view tab as a stack of blocks (see module docstring).

    fields / headers / breakout_values may be passed in when building several
    views in one run, so the inputs are read once, not per view (the
    read-quota matters).
    """
    v = _view_inputs(client, cfg, tab, granularity, fields, headers,
                     breakout_values)

    ensure_tab(client, tab)
    sheet_id = client.get_sheet_id(tab)
    client.clear_range(tab)
    page = Page(tab, sheet_id)

    _add_title(page, v)
    dim_specs, drop_positions, pickers = _add_filter_header(page, v)
    _add_compare_block(page, v, dim_specs)
    _add_kpi_strip(page, v, dim_specs)
    matrix = _add_period_matrix(page, v, dim_specs, pickers)
    _add_breakout_tables(page, v, dim_specs)
    _add_dropdowns(page, v, drop_positions)

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
    if granularity == "month" and matrix:
        header_row, first_data = matrix
        metric_value_cols = [1 + i for i in range(len(v.metric_fields))]
        client.batch_update([
            theme.line_chart_request(
                sheet_id, metric_value_cols, header_row - 1,
                (first_data - 1) + v.num_periods, v.kpi_last_col + 1,
            )
        ])

    return {
        "tab": tab,
        "granularity": granularity,
        "metrics": v.metric_names,
        "dimensions": v.dimensions,
        "breakouts": v.breakouts,
        "periods": v.num_periods,
    }


def build_views(client, cfg):
    """Build the three period views plus the Comparison tab.

    Setup fields, data_source headers, and (when any dimension is broken out)
    the Mapping values are read once here and passed to each view, so building
    everything stays a few reads, not several per tab. The date column is read
    only for the Comparison tab's default date range: the views' period
    windows are TODAY()-anchored formulas and need no data read.
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
        build_view(client, cfg, tab, granularity, fields, headers, breakout_values)
        for tab, granularity in view_specs(cfg)
    ]
    results.append(build_comparison(client, cfg, fields, headers, serials))
    return results
