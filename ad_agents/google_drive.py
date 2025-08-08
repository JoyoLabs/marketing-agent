from __future__ import annotations

import io
from typing import Optional

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

from .config import AppConfig


SCOPES = [
    "https://www.googleapis.com/auth/drive",
]


class DriveClient:
    def __init__(self, cfg: AppConfig):
        self._cfg = cfg
        service_account_info = cfg.get_service_account_info()
        if not service_account_info:
            raise RuntimeError(
                "Google Service Account credentials are required. Provide either GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_JSON_PATH."
            )
        credentials = Credentials.from_service_account_info(
            service_account_info, scopes=SCOPES
        )
        self._svc = build("drive", "v3", credentials=credentials, cache_discovery=False)

    def upload_png_bytes(self, data: bytes, filename: str) -> str:
        file_metadata = {
            "name": filename,
            "parents": [self._cfg.drive_folder_id],
        }
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype="image/png")
        created = (
            self._svc.files()
            .create(
                body=file_metadata,
                media_body=media,
                fields="id,webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )
        file_id = created["id"]
        # Make it public-readable
        self._svc.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            supportsAllDrives=True,
        ).execute()
        # Re-fetch to get webViewLink after permission update
        file = (
            self._svc.files()
            .get(
                fileId=file_id,
                fields="id,webViewLink,webContentLink",
                supportsAllDrives=True,
            )
            .execute()
        )
        return file.get("webViewLink") or file.get("webContentLink")

    def download_file_bytes(self, file_id: str) -> bytes:
        request = self._svc.files().get_media(fileId=file_id, supportsAllDrives=True)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        return fh.getvalue()

    def get_file_name(self, file_id: str) -> Optional[str]:
        try:
            meta = (
                self._svc.files()
                .get(fileId=file_id, fields="name", supportsAllDrives=True)
                .execute()
            )
            return meta.get("name")
        except Exception:
            return None

