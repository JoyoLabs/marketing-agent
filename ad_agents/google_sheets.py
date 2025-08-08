from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

import gspread
from google.oauth2.service_account import Credentials

from .config import AppConfig


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class SheetsClient:
    def __init__(self, cfg: AppConfig):
        self._cfg = cfg
        service_account_info = cfg.get_service_account_info()
        if not service_account_info:
            raise RuntimeError(
                "Google Service Account credentials are required. Provide either GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_JSON_PATH."
            )
        print("[blue]Initializing Google Sheets client...[/blue]")
        credentials = Credentials.from_service_account_info(
            service_account_info, scopes=SCOPES
        )
        self._gc = gspread.authorize(credentials)
        print("[green]Successfully authenticated with Google Sheets[/green]")
        # Caches to minimize repeated reads
        self._ideas_ws_cache = None
        self._campaign_ws_cache = None
        self._ideas_headers_cache: Optional[List[str]] = None
        self._campaign_headers_cache: Optional[List[str]] = None

    # App list sheet helpers
    def _open_app_list_ws(self):
        sh = self._gc.open_by_key(self._cfg.app_list_sheet_id)
        return sh.sheet1

    def _open_ideas_ws(self):
        if self._ideas_ws_cache is not None:
            return self._ideas_ws_cache
        sh = self._gc.open_by_key(self._cfg.ideas_sheet_id)
        self._ideas_ws_cache = sh.sheet1
        return self._ideas_ws_cache

    def _open_campaign_config_ws(self):
        if self._campaign_ws_cache is not None:
            return self._campaign_ws_cache
        sh = self._gc.open_by_key(self._cfg.campaign_config_sheet_id)
        self._campaign_ws_cache = sh.sheet1
        return self._campaign_ws_cache

    def _campaign_headers(self) -> List[str]:
        if self._campaign_headers_cache is not None:
            return self._campaign_headers_cache
        ws = self._open_campaign_config_ws()
        self._campaign_headers_cache = ws.row_values(1)
        return self._campaign_headers_cache

    def get_app_by_name(self, app_name: str) -> Optional[Dict[str, Any]]:
        ws = self._open_app_list_ws()
        rows = ws.get_all_records()
        for row in rows:
            if str(row.get("AppName", "")).strip().lower() == app_name.strip().lower():
                return row
        return None

    def list_apps(self) -> List[Dict[str, Any]]:
        return self._open_app_list_ws().get_all_records()

    def get_campaign_config_by_app(self, app_name: str) -> Optional[Dict[str, Any]]:
        # Row-wise read to avoid full-sheet reads
        ws = self._open_campaign_config_ws()
        headers = self._campaign_headers()
        col_a = ws.col_values(1)  # assume AppName is column A
        target = app_name.strip().lower()
        for idx, v in enumerate(col_a[1:], start=2):
            if str(v).strip().lower() == target:
                row_vals = ws.row_values(idx)
                row: Dict[str, Any] = {}
                for i, h in enumerate(headers):
                    if not h:
                        continue
                    row[h] = row_vals[i] if i < len(row_vals) else ""
                return row
        return None

    def get_campaign_config_row_index(self, app_name: str) -> Optional[int]:
        ws = self._open_campaign_config_ws()
        col_a = ws.col_values(1)
        target = app_name.strip().lower()
        for idx, v in enumerate(col_a[1:], start=2):
            if str(v).strip().lower() == target:
                return idx
        return None

    # Ideas row-wise helpers to avoid get_all_records
    def ideas_row_count(self) -> int:
        ws = self._open_ideas_ws()
        col_a = ws.col_values(1)
        return len(col_a)

    def read_ideas_row(self, row_index: int) -> Dict[str, Any]:
        ws = self._open_ideas_ws()
        headers = self._ideas_headers()
        vals = ws.row_values(row_index)
        row: Dict[str, Any] = {}
        for i, h in enumerate(headers):
            if not h:
                continue
            row[h] = vals[i] if i < len(vals) else ""
        row["_row_index"] = row_index
        return row

    def read_ideas_columns(self, column_names: List[str]) -> Dict[str, List[str]]:
        """Read specific columns from the ideas sheet using a single batch_get.

        Returns a dict mapping column name to list of values (starting at row 2).
        """
        def _col_letters(col_idx: int) -> str:
            # 1-indexed column to A1 letters (1->A, 27->AA)
            letters = ""
            n = col_idx
            while n > 0:
                n, r = divmod(n - 1, 26)
                letters = chr(65 + r) + letters
            return letters
        ws = self._open_ideas_ws()
        headers = self._ideas_headers()
        name_to_index: Dict[str, int] = {h: i + 1 for i, h in enumerate(headers) if h}
        ranges: List[str] = []
        valid_names: List[str] = []
        for name in column_names:
            col_idx = name_to_index.get(name)
            if not col_idx:
                continue
            col_letter = _col_letters(col_idx)
            ranges.append(f"{col_letter}2:{col_letter}")
            valid_names.append(name)
        if not ranges:
            return {name: [] for name in column_names}
        results = ws.batch_get(ranges)
        out: Dict[str, List[str]] = {}
        for name, values in zip(valid_names, results):
            # values is a list of lists (rows); flatten to single list of first cell per row
            flattened: List[str] = [row[0] if row else "" for row in values]
            out[name] = flattened
        # Ensure keys for all requested names
        for name in column_names:
            out.setdefault(name, [])
        return out

    def read_ideas_rows(self, row_indices: List[int]) -> List[Dict[str, Any]]:
        """Read specific rows from the ideas sheet with one batch_get.

        Returns a list of row dicts with '_row_index' populated.
        """
        ws = self._open_ideas_ws()
        headers = self._ideas_headers()
        ranges = [f"{idx}:{idx}" for idx in row_indices]
        if not ranges:
            return []
        results = ws.batch_get(ranges)
        rows: List[Dict[str, Any]] = []
        for idx, values in zip(row_indices, results):
            vals = values[0] if values else []
            row: Dict[str, Any] = {}
            for i, h in enumerate(headers):
                if not h:
                    continue
                row[h] = vals[i] if i < len(vals) else ""
            row["_row_index"] = idx
            rows.append(row)
        return rows

    # Ideas sheet helpers
    def _ideas_headers(self) -> List[str]:
        if self._ideas_headers_cache is not None:
            return self._ideas_headers_cache
        ws = self._open_ideas_ws()
        self._ideas_headers_cache = ws.row_values(1)
        return self._ideas_headers_cache

    def _ensure_headers(self):
        expected = [
            "ID",
            "Timestamp",
            "Status",
            "App_Name",
            "Target_Audience",
            "Platform",
            "Hook",
            "Idea",
            "Image_Prompt",
            "Image_URL",
            # Campaign fields appended by campaign agent
            "campaign_id",
            "adset_id",
            "creative_id",
            "ad_id",
            "image_hash",
        ]
        ws = self._open_ideas_ws()
        headers = self._ideas_headers()
        if headers != expected:
            if not headers:
                ws.append_row(expected)
                self._ideas_headers_cache = expected
            else:
                # Merge existing headers with expected while keeping order of expected
                existing = set([h for h in headers if h])
                merged = []
                for h in expected:
                    if h not in merged:
                        merged.append(h)
                ws.update("1:1", [merged])
                self._ideas_headers_cache = merged

    def next_idea_id(self) -> int:
        ws = self._open_ideas_ws()
        ids = ws.col_values(1)[1:]  # skip header
        max_id = 0
        for v in ids:
            try:
                max_id = max(max_id, int(v))
            except Exception:
                continue
        return max_id + 1

    def append_ideas(
        self,
        app_name: str,
        ideas: List[Dict[str, str]],
        platform: Optional[str] = None,
    ) -> Tuple[int, int]:
        """Append ideas to the ideas sheet.

        Returns: (first_row_index, count)
        """
        self._ensure_headers()
        ws = self._open_ideas_ws()
        now = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        next_id = self.next_idea_id()
        platform_value = platform or "Meta"
        rows = []
        for idea in ideas:
            rows.append(
                [
                    next_id,
                    now,
                    "Ideated",
                    app_name,
                    idea.get("target_audience", ""),
                    idea.get("platform", platform_value),
                    idea.get("hook", ""),
                    idea.get("idea", ""),
                    idea.get("image_prompt", ""),
                    "",
                ]
            )
            next_id += 1
        if not rows:
            return (0, 0)
        ws.append_rows(rows, value_input_option="RAW")
        # Determine first new row index
        first_row = len(ws.get_all_values()) - len(rows) + 1
        return (first_row, len(rows))

    def list_ideated(self) -> List[Dict[str, Any]]:
        ws = self._open_ideas_ws()
        all_rows = ws.get_all_records()
        result = []
        for idx, row in enumerate(all_rows, start=2):  # header is row 1
            if str(row.get("Status", "")).strip().lower() == "ideated":
                row_copy = dict(row)
                row_copy["_row_index"] = idx
                result.append(row_copy)
        return result

    def update_row(self, row_index: int, updates: Dict[str, Any]) -> None:
        from gspread.utils import rowcol_to_a1
        ws = self._open_ideas_ws()
        headers = self._ideas_headers()
        requests: List[Dict[str, Any]] = []
        for key, value in updates.items():
            if key not in headers:
                continue
            col = headers.index(key) + 1
            a1 = rowcol_to_a1(row_index, col)
            requests.append({"range": a1, "values": [[value]]})
        if requests:
            ws.batch_update(requests)

    def update_campaign_config_row(self, row_index: int, updates: Dict[str, Any]) -> None:
        from gspread.utils import rowcol_to_a1
        ws = self._open_campaign_config_ws()
        headers = self._campaign_headers()
        requests: List[Dict[str, Any]] = []
        for key, value in updates.items():
            if key not in headers:
                continue
            col = headers.index(key) + 1
            a1 = rowcol_to_a1(row_index, col)
            requests.append({"range": a1, "values": [[value]]})
        if requests:
            ws.batch_update(requests)

    def batch_update_ideas_rows(self, updates_by_row_index: Dict[int, Dict[str, Any]]) -> None:
        from gspread.utils import rowcol_to_a1
        if not updates_by_row_index:
            return
        ws = self._open_ideas_ws()
        headers = self._ideas_headers()
        requests: List[Dict[str, Any]] = []
        for row_index, updates in updates_by_row_index.items():
            for key, value in updates.items():
                if key not in headers:
                    continue
                col = headers.index(key) + 1
                a1 = rowcol_to_a1(row_index, col)
                requests.append({"range": a1, "values": [[value]]})
        if requests:
            ws.batch_update(requests)

