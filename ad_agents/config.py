from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv


# Load .env robustly (handle malformed or UTF-16 files without crashing CLI)
try:
    load_dotenv(override=True)
except Exception:
    try:
        load_dotenv(override=True, encoding="utf-16")
    except Exception:
        # As a last resort, continue without .env to allow --help and non-env usage
        pass


@dataclass(frozen=True)
class AppConfig:
    openai_api_key: str
    app_list_sheet_id: str
    ideas_sheet_id: str
    campaign_config_sheet_id: str
    drive_folder_id: str
    drive_video_folder_id: str
    competitor_creatives_folder_id: str
    makevideos_sheet_id: str
    creative_analysis_sheet_id: Optional[str]
    google_service_account_json_path: Optional[str]
    google_service_account_json: Optional[str]
    default_platform: str = "Meta"
    default_num_ideas: int = 10

    @staticmethod
    def load_from_env() -> "AppConfig":
        return AppConfig(
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            app_list_sheet_id=os.environ.get(
                "APP_LIST_SHEET_ID", "13QQF7vVGiSr07U1Jcj-fR3plPXyX0D-0iVc0Ei8YjxI"
            ),
            ideas_sheet_id=os.environ.get(
                "IDEAS_SHEET_ID", "1JaVenG582kvZwaS3noxHGwbGu1-mNtv_mwvaljZUMJo"
            ),
            campaign_config_sheet_id=os.environ.get(
                "CAMPAIGN_CONFIG_SHEET_ID", "1RooxSyfx4Ip47pwIdDErEFbt8lePBixm8KEAruT2H4E"
            ),
            drive_folder_id=os.environ.get(
                "DRIVE_FOLDER_ID", "1aAKwxkSl3z_tcu_AgXB4WJSAL50BKWML"
            ),
            drive_video_folder_id=os.environ.get(
                "DRIVE_VIDEO_FOLDER_ID", ""
            ),
            competitor_creatives_folder_id=os.environ.get(
                "COMPETITOR_CREATIVES_FOLDER_ID", ""
            ),
            makevideos_sheet_id=os.environ.get(
                "MAKEVIDEOS_SHEET_ID", ""
            ),
            creative_analysis_sheet_id=os.environ.get(
                "CREATIVE_ANALYSIS_SHEET_ID"
            ),
            google_service_account_json_path=os.environ.get(
                "GOOGLE_SERVICE_ACCOUNT_JSON_PATH"
            ),
            google_service_account_json=os.environ.get(
                "GOOGLE_SERVICE_ACCOUNT_JSON"
            ),
            default_platform=os.environ.get("TARGET_PLATFORM", "Meta"),
            default_num_ideas=int(os.environ.get("NUM_IDEAS", "10")),
        )

    def get_service_account_info(self) -> Optional[dict]:
        if self.google_service_account_json:
            try:
                return json.loads(self.google_service_account_json)
            except json.JSONDecodeError:
                raise ValueError(
                    "GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON."
                )
        if self.google_service_account_json_path and os.path.exists(
            self.google_service_account_json_path
        ):
            with open(self.google_service_account_json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

