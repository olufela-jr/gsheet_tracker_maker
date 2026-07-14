"""The BigQuery audit trail: one row per tracker created or set up."""

# Columns of the BigQuery audit table, in schema order.
TRACKER_FIELDS = [
    "event_id",
    "created_at",
    "spreadsheet_id",
    "url",
    "title",
    "client",
    "sub_brand",
    "created_by",
    "status",
    "service_revision",
]


def build_tracker_record(
    event_id,
    created_at,
    spreadsheet_id,
    url,
    title,
    client,
    sub_brand,
    created_by,
    status="active",
    service_revision="",
):
    """Build the audit row dict, with blanks for any missing value."""
    values = {
        "event_id": event_id,
        "created_at": created_at,
        "spreadsheet_id": spreadsheet_id,
        "url": url,
        "title": title,
        "client": client,
        "sub_brand": sub_brand,
        "created_by": created_by,
        "status": status,
        "service_revision": service_revision,
    }
    return {field: (values.get(field) or "") for field in TRACKER_FIELDS}


def log_tracker(bq_client, cfg, record):
    """Stream one audit row into the BigQuery trackers table."""
    bq_client.insert_row(cfg.bigquery_dataset, cfg.bigquery_table, record)
    return record
