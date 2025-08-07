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

    # App list sheet helpers
    def _open_app_list_ws(self):
        sh = self._gc.open_by_key(self._cfg.app_list_sheet_id)
        return sh.sheet1

    def _open_ideas_ws(self):
        sh = self._gc.open_by_key(self._cfg.ideas_sheet_id)
        return sh.sheet1

    def get_app_by_name(self, app_name: str) -> Optional[Dict[str, Any]]:
        ws = self._open_app_list_ws()
        rows = ws.get_all_records()
        for row in rows:
            if str(row.get("AppName", "")).strip().lower() == app_name.strip().lower():
                return row
        return None

    def list_apps(self) -> List[Dict[str, Any]]:
        return self._open_app_list_ws().get_all_records()

    # Ideas sheet helpers
    def _ideas_headers(self) -> List[str]:
        ws = self._open_ideas_ws()
        return ws.row_values(1)

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
        ]
        ws = self._open_ideas_ws()
        headers = ws.row_values(1)
        if headers != expected:
            if not headers:
                ws.append_row(expected)
            else:
                # Overwrite to ensure correct order
                ws.update("1:1", [expected])

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
        ws = self._open_ideas_ws()
        headers = self._ideas_headers()
        cells = []
        for key, value in updates.items():
            if key not in headers:
                continue
            col = headers.index(key) + 1
            cells.append({"range": f"{row_index}:{row_index}", "values": [[]]})
            ws.update_cell(row_index, col, value)

