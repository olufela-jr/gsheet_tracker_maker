"""Thin wrapper over the Google Sheets API v4.

Authenticates with Application Default Credentials, which on Cloud Run resolve
to the runtime service account. That service account must be shared as an
Editor on each tracker sheet for these calls to succeed.

The wrapper holds no per-sheet state beyond the spreadsheet id it was built
with. One instance is created per request and discarded after.
"""

import google.auth
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsClient:
    def __init__(self, spreadsheet_id=None, service=None):
        # spreadsheet_id is optional so one service object can be reused to
        # build a second client pointed at the registry spreadsheet.
        self.spreadsheet_id = spreadsheet_id
        # Request-scoped cache of spreadsheet metadata. get_spreadsheet is called
        # many times per run (existing_titles, get_sheet_id, get_named_ranges,
        # chart ids); caching it collapses those into one read and keeps us under
        # the Sheets "read requests per minute per user" quota. Any batch_update
        # may change structure or named ranges, so it clears the cache.
        self._meta_cache = None
        if service is None:
            credentials, _ = google.auth.default(scopes=SCOPES)
            service = build(
                "sheets", "v4", credentials=credentials, cache_discovery=False
            )
        self.service = service

    @property
    def _values(self):
        return self.service.spreadsheets().values()

    def read_range(self, a1_range, unformatted=False):
        """Read a range and return its rows. Empty range returns [].

        unformatted=True asks for UNFORMATTED_VALUE, so dates come back as
        serial numbers (needed for bucketing) rather than formatted strings.
        """
        render = "UNFORMATTED_VALUE" if unformatted else "FORMATTED_VALUE"
        response = (
            self._values.get(
                spreadsheetId=self.spreadsheet_id,
                range=a1_range,
                valueRenderOption=render,
            )
            .execute()
        )
        return response.get("values", [])

    def write_values(self, a1_range, values, value_input_option="RAW"):
        """Write one block of values to a single range."""
        body = {"values": values}
        return (
            self._values.update(
                spreadsheetId=self.spreadsheet_id,
                range=a1_range,
                valueInputOption=value_input_option,
                body=body,
            )
            .execute()
        )

    def batch_write_values(self, data, value_input_option="RAW"):
        """Write several ranges at once.

        Each item in data is {"range": "...", "values": [[...]]}. Pass
        value_input_option="USER_ENTERED" for formulas so they evaluate
        instead of being stored as literal text.
        """
        body = {"valueInputOption": value_input_option, "data": data}
        return (
            self._values.batchUpdate(spreadsheetId=self.spreadsheet_id, body=body)
            .execute()
        )

    def clear_range(self, a1_range):
        """Clear cell values in a range. Passing a bare tab name clears the
        whole tab. This clears values only, not data validation rules.
        """
        return (
            self._values.clear(
                spreadsheetId=self.spreadsheet_id, range=a1_range, body={}
            )
            .execute()
        )

    def get_spreadsheet(self):
        """Fetch spreadsheet metadata: sheet properties and named ranges.

        Cached for the life of this client (one request). batch_update clears
        the cache, since adding tabs or named ranges changes this metadata.
        """
        if self._meta_cache is None:
            self._meta_cache = (
                self.service.spreadsheets()
                .get(spreadsheetId=self.spreadsheet_id)
                .execute()
            )
        return self._meta_cache

    def get_sheet_id(self, title):
        """Return the numeric sheetId for a tab title, or None if absent.

        Matching is case-insensitive, mirroring how the Sheets API resolves tab
        names in A1 ranges.
        """
        meta = self.get_spreadsheet()
        for sheet in meta.get("sheets", []):
            props = sheet.get("properties", {})
            if (props.get("title") or "").lower() == title.lower():
                return props.get("sheetId")
        return None

    def get_named_ranges(self):
        """Return a dict of name -> named range definition."""
        meta = self.get_spreadsheet()
        return {nr["name"]: nr for nr in meta.get("namedRanges", [])}

    def batch_update(self, requests):
        """Run a list of batchUpdate requests (named ranges, data validation)."""
        body = {"requests": requests}
        result = (
            self.service.spreadsheets()
            .batchUpdate(spreadsheetId=self.spreadsheet_id, body=body)
            .execute()
        )
        # Structure or named ranges may have changed; drop the metadata cache.
        self._meta_cache = None
        return result

