from __future__ import annotations

from typing import Dict, List, Optional
import os

from rich.console import Console

from .config import AppConfig
from .google_drive import DriveClient
from .google_sheets import SheetsClient
from .meta_client import MetaClient, MetaConfig


console = Console()


TARGETING_PH_ANDROID = {
    "geo_locations": {"countries": ["PH"]},
    "publisher_platforms": [
        "facebook",
        "instagram",
        "audience_network",
        "messenger",
    ],
    "device_platforms": ["mobile"],
    "user_os": ["Android_ver_10.0_and_above"],
}


class VideoCampaignAgent:
    def __init__(self, cfg: AppConfig):
        self._cfg = cfg
        self._sheets = SheetsClient(cfg)
        self._drive = DriveClient(cfg)
        # Lazy init Meta client only when creating campaigns
        self._meta: Optional[MetaClient] = None

    def scan_videos(self, default_app_name: Optional[str], default_campaign_type: Optional[str]) -> int:
        if not self._cfg.drive_video_folder_id:
            raise RuntimeError("DRIVE_VIDEO_FOLDER_ID is not configured")
        files = self._drive.list_videos_in_folder(self._cfg.drive_video_folder_id)
        rows: List[Dict] = []
        for f in files:
            rows.append(
                {
                    "App_Name": default_app_name or "",
                    "File_ID": f.get("id"),
                    "File_Name": f.get("name"),
                    "File_URL": f.get("webViewLink", ""),
                    "MimeType": f.get("mimeType", ""),
                    "ModifiedTime": f.get("modifiedTime", ""),
                    "CampaignType": default_campaign_type or "",
                }
            )
        inserted = self._sheets.upsert_videos_by_file_id(rows)
        console.print(f"[green]Discovered and inserted {inserted} new video rows[/green]")
        return inserted

    def _build_campaign_name(self, app_name: str, cfg_row: Dict[str, str]) -> str:
        from datetime import datetime
        network = "Meta"
        platform = "Android"
        data_source = "MakeCom"
        geo = cfg_row.get("Geo", "PH")
        targeting_label = cfg_row.get("Targeting", "AutoPlacements")
        campaign_type = cfg_row.get("CampaignType", "AIVideoTesting")
        date_str = datetime.utcnow().strftime("%d%m%y")
        base = f"{app_name}_{network}_{platform}_{data_source}_{geo}_{targeting_label}_{campaign_type}"
        latest_id_val = 1
        try:
            latest_id_val = int(str(cfg_row.get("Latest_Campaign_ID", "1")).strip() or "1")
        except Exception:
            latest_id_val = 1
        return f"{base}_{latest_id_val}_{date_str}"

    def create_video_campaigns(
        self,
        app_name: str,
        campaign_type: str,
        n: int,
        budget_minor_override: Optional[int] = None,
    ) -> int:
        # Pull config for (AppName, CampaignType)
        cfg_row = self._sheets.get_campaign_config_by_app_and_type(app_name, campaign_type)
        if not cfg_row:
            raise RuntimeError(f"Campaign config not found for app {app_name} and type {campaign_type}")
        # Build Meta config from env (app credentials) + sheet (account/page/app ids)
        import os
        fb_app_id = os.environ.get("FB_APP_ID") or os.environ.get("FP_APP_ID")
        fb_app_secret = os.environ.get("FB_APP_SECRET")
        fb_access_token = os.environ.get("FB_ACCESS_TOKEN")
        missing = [k for k, v in {
            "FB_APP_ID/FP_APP_ID": fb_app_id,
            "FB_APP_SECRET": fb_app_secret,
            "FB_ACCESS_TOKEN": fb_access_token,
        }.items() if not v]
        if missing:
            raise KeyError(f"Missing Meta env vars: {', '.join(missing)}")

        sheet_missing = [k for k, v in {
            "AD_ACCOUNT_ID": cfg_row.get("AD_ACCOUNT_ID"),
            "FB_PAGE_ID": cfg_row.get("FB_PAGE_ID"),
            "Meta_App_ID": cfg_row.get("Meta_App_ID"),
            "GOOGLE_PLAY_URL": cfg_row.get("GOOGLE_PLAY_URL"),
            "INSTAGRAM_ID": cfg_row.get("INSTAGRAM_ID"),
        }.items() if not v]
        if sheet_missing:
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
        # Initialize Meta client
        self._meta = MetaClient(meta_cfg)
        # Budget
        daily_budget_minor = budget_minor_override
        if daily_budget_minor is None:
            try:
                usd = float(cfg_row.get("Daily_Budget", "10") or 10)
            except Exception:
                usd = 10.0
            daily_budget_minor = int(round(usd * 100))

        # Gather videos to process
        rows = self._sheets.list_new_videos(app_name=app_name, campaign_type=campaign_type, limit=n)
        if not rows:
            console.print("[yellow]No new videos to process[/yellow]")
            return 0

        # Create campaign and ad set once
        campaign_name = self._build_campaign_name(app_name, cfg_row)
        campaign_id = self._meta.create_campaign(campaign_name)
        adset_name = f"{campaign_name}_AS"
        # Create default ad set (installs), then patch it to optimize for Purchase app event
        adset_id = self._meta.create_adset(
            name=adset_name,
            campaign_id=campaign_id,
            daily_budget_minor=daily_budget_minor,
            targeting_spec=TARGETING_PH_ANDROID,
        )
        try:
            from facebook_business.adobjects.adset import AdSet as FBAdSet
            adset = FBAdSet(adset_id)
            adset.update({
                FBAdSet.Field.optimization_goal: FBAdSet.OptimizationGoal.offsite_conversions,
                FBAdSet.Field.promoted_object: {
                    "application_id": self._meta._cfg.android_app_id,
                    "object_store_url": self._meta._cfg.google_play_url,
                    "custom_event_type": "PURCHASE",
                },
            })
            adset.remote_update()
        except Exception as e:
            console.print(f"[yellow]Warning: failed to switch ad set to PURCHASE optimization: {e}[/yellow]")

        # Update Latest_Campaign_ID
        cfg_idx = self._sheets.get_campaign_config_row_index_by_app_and_type(app_name, campaign_type)
        if cfg_idx:
            try:
                latest = int(str(cfg_row.get("Latest_Campaign_ID", "1") or "1")) + 1
            except Exception:
                latest = 1
            self._sheets.update_campaign_config_row(cfg_idx, {"Latest_Campaign_ID": latest})

        updates: Dict[int, Dict[str, str]] = {}
        created = 0
        for row in rows:
            row_index = row["_row_index"]
            file_id = str(row.get("File_ID", ""))
            try:
                # Download video and upload to Meta
                video_bytes = self._drive.download_file_bytes(file_id)
                video_id = self._meta.upload_video_from_bytes(video_bytes)

                # Provide thumbnail: prefer configured hash, else use video's preferred thumbnail url
                thumb_hash = os.environ.get("VIDEO_THUMB_IMAGE_HASH")
                thumb_url = None if thumb_hash else self._meta.get_video_thumbnail_url(video_id)

                # Names
                drive_name = self._drive.get_file_name(file_id) or "Video_Unknown"
                creative_name = f"CN_AI_{drive_name}"
                ad_name = creative_name
                message = str(row.get("Primary_Text") or "Try it free")

                # Creative + Ad with thumbnail. Retry once by uploading logo from Drive if provided.
                try:
                    creative_id = self._meta.create_video_creative(
                        creative_name,
                        video_id,
                        message,
                        image_url=thumb_url,
                        image_hash=thumb_hash,
                    )
                except Exception as e:
                    needs_thumb = (
                        "needs a video thumbnail" in str(e).lower()
                        or "image not found" in str(e).lower()
                    )
                    logo_drive_id = os.environ.get("VIDEO_THUMB_DRIVE_FILE_ID")
                    if needs_thumb and logo_drive_id:
                        try:
                            logo_bytes = self._drive.download_file_bytes(logo_drive_id)
                            uploaded_hash = self._meta.upload_image_from_bytes(logo_bytes, filename="logo.png")
                            creative_id = self._meta.create_video_creative(
                                creative_name,
                                video_id,
                                message,
                                image_hash=uploaded_hash,
                            )
                        except Exception:
                            raise
                    else:
                        raise
                ad_id = self._meta.create_ad(name=ad_name, adset_id=adset_id, creative_id=creative_id)

                updates[row_index] = {
                    "Status": "Created",
                    "campaign_id": campaign_id,
                    "adset_id": adset_id,
                    "creative_id": creative_id,
                    "ad_id": ad_id,
                    "video_id": video_id,
                }
                created += 1
                console.print(f"[green]Created video ad for row {row_index}[/green]")
            except Exception as e:  # noqa: BLE001
                updates[row_index] = {
                    "Status": "Failed",
                    "notes": str(e),
                }
                console.print(f"[red]Failed row {row_index}: {e}[/red]")

        if updates:
            self._sheets.update_videos_rows(updates)
        return created


