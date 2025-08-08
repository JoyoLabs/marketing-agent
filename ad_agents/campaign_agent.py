from __future__ import annotations

import io
import re
import os
from typing import Any, Dict, List, Optional

from rich.console import Console
from tenacity import retry, stop_after_attempt, wait_exponential

from .google_sheets import SheetsClient
from .google_drive import DriveClient
from .config import AppConfig
from .meta_client import MetaClient, MetaConfig


console = Console()

TARGETING_PH_ANDROID: Dict[str, Any] = {
    "geo_locations": {"countries": ["PH"]},
    "age_min": 18,
    "device_platforms": ["mobile"],
    "user_os": ["Android_ver_10.0_and_above"],
    # allow automatic placements by specifying platforms only
    "publisher_platforms": ["facebook", "instagram", "audience_network", "messenger"],
}


def _extract_drive_file_id(url: str) -> Optional[str]:
    m = re.search(r"/d/([\w-]+)/", url)
    if m:
        return m.group(1)
    m = re.search(r"id=([\w-]+)", url)
    if m:
        return m.group(1)
    return None


def _build_ad_asset_name(filename: str, app_name: str) -> str:
    base = os.path.splitext(filename)[0]
    parts = [p for p in base.split("_") if p]
    filtered: List[str] = []
    for token in parts:
        lower = token.strip().lower()
        if lower == "image":
            continue
        if app_name and lower == app_name.strip().lower():
            continue
        filtered.append(token)
    for i, token in enumerate(filtered):
        if token.isdigit():
            filtered[i] = token.zfill(4)
            break
    core = "_".join(filtered) if filtered else base
    return f"CN_AI_{core}"


class CampaignAgent:
    def __init__(self, cfg: AppConfig):
        self._cfg = cfg
        self._sheets = SheetsClient(cfg)
        self._drive = DriveClient(cfg)

    def _download_image_bytes(self, drive_url: str) -> bytes:
        file_id = _extract_drive_file_id(drive_url)
        if not file_id:
            raise ValueError(f"Could not extract Drive file id from URL: {drive_url}")
        return self._drive.download_file_bytes(file_id)

    @retry(wait=wait_exponential(multiplier=1, min=1, max=20), stop=stop_after_attempt(5))
    def _create_one(self, row: Dict[str, Any], budget_minor: int) -> Dict[str, str]:
        idea_id = str(row.get("ID"))
        hook = str(row.get("Hook", "")).strip().replace(" ", "")

        drive_url = str(row.get("Image_URL", ""))
        image_bytes = self._download_image_bytes(drive_url)

        # Campaign naming via config sheet
        app_name = str(row.get("App_Name", "")).strip()
        cfg_row = self._sheets.get_campaign_config_by_app(app_name) or {}
        network = cfg_row.get("Network", "Meta")
        platform = cfg_row.get("Platform", "Android")
        data_source = cfg_row.get("DataSource", "Data")
        geo = cfg_row.get("Geo", "PH")
        targeting_label = cfg_row.get("Targeting", "PH-Android")
        campaign_type = cfg_row.get("CampaignType", "Test")
        from datetime import datetime

        date_str = datetime.utcnow().strftime("%d%m%y")
        campaign_name = (
            f"{app_name}_{network}_{platform}_{data_source}_{geo}_{targeting_label}_{campaign_type}_{date_str}"
        ).replace("__", "_")

        # Build Meta config from env + sheet with fallbacks
        fb_app_id = os.environ.get("FB_APP_ID") or os.environ.get("FP_APP_ID")
        fb_app_secret = os.environ.get("FB_APP_SECRET")
        fb_access_token = os.environ.get("FB_ACCESS_TOKEN")
        missing = [k for k, v in {
            "FB_APP_ID/FP_APP_ID": fb_app_id,
            "FB_APP_SECRET": fb_app_secret,
            "FB_ACCESS_TOKEN": fb_access_token,
        }.items() if not v]
        if missing:
            console.print(f"[red]Missing Meta env vars: {', '.join(missing)}[/red]")
            raise KeyError(f"Missing Meta env vars: {', '.join(missing)}")

        # Check campaign config sheet fields
        sheet_missing = [k for k, v in {
            "AD_ACCOUNT_ID": cfg_row.get("AD_ACCOUNT_ID"),
            "FB_PAGE_ID": cfg_row.get("FB_PAGE_ID"),
            "Meta_App_ID": cfg_row.get("Meta_App_ID"),
            "GOOGLE_PLAY_URL": cfg_row.get("GOOGLE_PLAY_URL"),
            "INSTAGRAM_ID": cfg_row.get("INSTAGRAM_ID"),
        }.items() if not v]
        if sheet_missing:
            console.print(f"[red]Missing campaign config fields for {app_name}: {', '.join(sheet_missing)}[/red]")
            raise KeyError(f"Missing campaign config fields: {', '.join(sheet_missing)}")

        meta_cfg = MetaConfig(
            app_id=str(fb_app_id),
            app_secret=str(fb_app_secret),
            access_token=str(fb_access_token),
            ad_account_id=str(cfg_row.get("AD_ACCOUNT_ID")),
            page_id=str(cfg_row.get("FB_PAGE_ID")),
            android_app_id=str(cfg_row.get("Meta_App_ID")),
            google_play_url=str(cfg_row.get("GOOGLE_PLAY_URL")),
            instagram_id=str(cfg_row.get("INSTAGRAM_ID")),
            api_version=os.environ.get("FB_API_VERSION", "v23.0"),
        )
        meta = MetaClient(meta_cfg)

        # Derive filename from Drive if possible to use in ad/creative name
        file_id = _extract_drive_file_id(drive_url)
        filename = None
        if file_id:
            filename = self._drive.get_file_name(file_id)
        if not filename:
            hook = row.get("Hook", "image")
            filename = f"Image_{row.get('App_Name', 'app')}_{hook}.png"
        image_hash = meta.upload_image_from_bytes(image_bytes, filename)
        campaign_id = meta.create_campaign(campaign_name)

        # Budget: numbers in sheet represent dollars; convert to minor units
        daily_budget_minor = budget_minor
        val = str(cfg_row.get("Daily_Budget", "")).strip()
        try:
            if val:
                dollars = float(val)
                daily_budget_minor = int(round(dollars * 100))
        except Exception:
            pass

        adset_id = meta.create_adset(
            name=f"{campaign_name} | ASet",
            campaign_id=campaign_id,
            daily_budget_minor=daily_budget_minor,
            targeting_spec=TARGETING_PH_ANDROID,
        )
        # Unified asset name for both creative and ad
        ad_asset_name = _build_ad_asset_name(filename, app_name)
        creative_id = meta.create_creative(
            name=ad_asset_name,
            image_hash=image_hash,
            message="Try it free",
        )
        ad_id = meta.create_ad(
            name=ad_asset_name,
            adset_id=adset_id,
            creative_id=creative_id,
        )
        return {
            "campaign_id": campaign_id,
            "adset_id": adset_id,
            "creative_id": creative_id,
            "ad_id": ad_id,
            "image_hash": image_hash,
        }

    def run(self, n: int = 1, budget_minor: int = 300) -> int:
        ws = self._sheets._open_ideas_ws()
        all_rows = ws.get_all_records()
        candidates: List[Dict[str, Any]] = []
        for idx, row in enumerate(all_rows, start=2):
            if str(row.get("Status", "")).lower() == "uploaded" and not row.get("campaign_id"):
                row_copy = dict(row)
                row_copy["_row_index"] = idx
                candidates.append(row_copy)

        if not candidates:
            console.print("[yellow]No uploaded rows without campaigns found[/yellow]")
            return 0

        # Group by app name so all selected rows for the same app share one campaign/ad set
        app_name = str(candidates[0].get("App_Name", "")).strip()
        group = [r for r in candidates if str(r.get("App_Name", "")).strip() == app_name][:n]

        if not group:
            return 0

        # Use the first row to create the campaign/ad set, then attach all ads under it
        try:
            ids = self._create_one(group[0], budget_minor=budget_minor)
            campaign_id = ids["campaign_id"]
            adset_id = ids["adset_id"]
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]Failed to create base campaign/ad set: {e}[/red]")
            return 0

        created = 0
        # First row already created; update its sheet row
        try:
            row0 = group[0]
            row_index0 = row0["_row_index"]
            for k in ("campaign_id", "adset_id", "creative_id", "ad_id", "image_hash", "Status"):
                self._sheets.update_row(row_index0, {k: ids.get(k, "")})
            created += 1
        except Exception:
            pass

        # For remaining rows, only create creative + ad under the existing campaign/adset
        meta = None
        # Rebuild meta client from the same app config row
        cfg_row = self._sheets.get_campaign_config_by_app(app_name) or {}
        fb_app_id = os.environ.get("FB_APP_ID") or os.environ.get("FP_APP_ID")
        fb_app_secret = os.environ.get("FB_APP_SECRET")
        fb_access_token = os.environ.get("FB_ACCESS_TOKEN")
        meta_cfg = MetaConfig(
            app_id=str(fb_app_id),
            app_secret=str(fb_app_secret),
            access_token=str(fb_access_token),
            ad_account_id=str(cfg_row.get("AD_ACCOUNT_ID")),
            page_id=str(cfg_row.get("FB_PAGE_ID")),
            android_app_id=str(cfg_row.get("Meta_App_ID")),
            google_play_url=str(cfg_row.get("GOOGLE_PLAY_URL")),
            instagram_id=str(cfg_row.get("INSTAGRAM_ID")),
            api_version=os.environ.get("FB_API_VERSION", "v23.0"),
        )
        meta = MetaClient(meta_cfg)

        for row in group[1:]:
            try:
                drive_url = str(row.get("Image_URL", ""))
                image_bytes = self._download_image_bytes(drive_url)
                file_id = _extract_drive_file_id(drive_url)
                filename = self._drive.get_file_name(file_id) if file_id else None
                if not filename:
                    hook = row.get("Hook", "image")
                    filename = f"Image_{row.get('App_Name', 'app')}_{hook}.png"
                image_hash = meta.upload_image_from_bytes(image_bytes, filename)

                ad_asset_name = _build_ad_asset_name(filename, app_name)
                creative_id = meta.create_creative(
                    name=ad_asset_name,
                    image_hash=image_hash,
                    message="Try it free",
                )
                ad_id = meta.create_ad(
                    name=ad_asset_name,
                    adset_id=adset_id,
                    creative_id=creative_id,
                )
                row_index = row["_row_index"]
                updates = {
                    "campaign_id": campaign_id,
                    "adset_id": adset_id,
                    "creative_id": creative_id,
                    "ad_id": ad_id,
                    "image_hash": image_hash,
                    "Status": "CampaignCreated",
                }
                for k, v in updates.items():
                    self._sheets.update_row(row_index, {k: v})
                created += 1
                console.print(f"[green]Added ad to campaign for row {row_index}[/green]")
            except Exception as e:  # noqa: BLE001
                console.print(f"[red]Failed row {row.get('_row_index')}: {e}[/red]")
                continue

        return created


