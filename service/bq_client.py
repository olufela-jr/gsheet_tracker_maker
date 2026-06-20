"""Thin wrapper over the BigQuery client for the tracker audit log.

Uses Application Default Credentials, which on Cloud Run resolve to the runtime
service account. That account needs BigQuery Data Editor on the dataset to
stream rows in.
"""

from google.cloud import bigquery


class BigQueryClient:
    def __init__(self, project=None, client=None):
        # project None lets the library fall back to the ADC project.
        self.client = client or bigquery.Client(project=project or None)

    def insert_row(self, dataset, table, row):
        """Stream one row into project.dataset.table.

        Raises if BigQuery reports per-row errors so the caller can surface a
        logging failure instead of silently dropping the audit record.
        """
        table_ref = "{}.{}.{}".format(self.client.project, dataset, table)
        errors = self.client.insert_rows_json(table_ref, [row])
        if errors:
            raise RuntimeError("BigQuery insert failed: {}".format(errors))

    def created_by(self, dataset, table, spreadsheet_id):
        """Return the most recent created_by for a tracker, or None if unknown.

        Used for per-spreadsheet authorization. Needs the BigQuery Job User role
        to run the query. Rows still in the streaming buffer may not appear yet.
        """
        query = (
            "SELECT created_by FROM `{p}.{d}.{t}` "
            "WHERE spreadsheet_id = @id ORDER BY created_at DESC LIMIT 1"
        ).format(p=self.client.project, d=dataset, t=table)
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("id", "STRING", spreadsheet_id)
            ]
        )
        rows = list(self.client.query(query, job_config=job_config).result())
        return rows[0]["created_by"] if rows else None
