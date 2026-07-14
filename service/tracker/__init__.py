"""Domain logic for the performance tracker generator.

All functions here are stateless. They take a sheets client and a Config and
do their work against the live sheet. The genuinely pure helpers (formulas)
take no client and are unit tested on their own.

The package is split by responsibility:
    formulas    pure SUMIFS / calc / date-bucket builders, number formats
    fields      reading the Setup tab and Data Source headers, validation
    common      shared tab-building plumbing (Page buffer, reads, dropdowns)
    scaffold    tab scaffolding, Mapping generation, named ranges
    views       the daily / weekly / monthly view tabs
    comparison  the split-screen Comparison tab
    audit       the BigQuery audit trail

Everything public is re-exported here, so `import tracker` and
`from tracker import ...` work as they did when this was one module.
"""

from .audit import TRACKER_FIELDS, build_tracker_record, log_tracker
from .comparison import COMPARISON_PERIODS, build_comparison
from .fields import (
    Field,
    ValidationError,
    breakout_dimensions_of,
    date_field_of,
    dimensions_of,
    mapping_dimensions_of,
    metrics_of,
    read_data_source_headers,
    read_setup,
    validate,
)
from .formulas import (
    DATE_FORMAT,
    DELTA_FORMAT,
    MONTH_FORMAT,
    bucket_serial,
    bucket_sumifs_expr,
    build_calc_formula,
    build_sumifs_formula,
    calc_expr,
    date_to_serial,
    distinct_buckets,
    distinct_values,
    formula_tokens,
    number_format_pattern,
    serial_to_date,
    sumifs_expr,
)
from .scaffold import (
    create_named_ranges,
    ensure_tab,
    existing_titles,
    generate_mapping,
    require_input_tabs,
    scaffold,
)
from .views import MAX_BREAKOUT_VALUES, build_view, build_views, view_specs


def run_all(client, cfg):
    """Run validate, generate_mapping, create_named_ranges, build_views.

    Named ranges are created before the views so the SUMIFS references resolve.
    validate runs first and raises before any writes happen.
    """
    validate(client, cfg)
    mapping = generate_mapping(client, cfg)
    named_ranges = create_named_ranges(client, cfg)
    views = build_views(client, cfg)
    return {
        "mapping": mapping,
        "named_ranges": named_ranges,
        "views": views,
    }
