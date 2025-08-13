from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional

from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.campaign import Campaign
from facebook_business.adobjects.adset import AdSet
from facebook_business.adobjects.adimage import AdImage
from facebook_business.adobjects.adcreative import AdCreative
from facebook_business.adobjects.advideo import AdVideo
from facebook_business.adobjects.ad import Ad


@dataclass(frozen=True)
class MetaConfig:
    app_id: str
    app_secret: str
    access_token: str
    ad_account_id: str  # format: act_...
    page_id: str
    android_app_id: str  # Meta App ID (numeric)
    google_play_url: str
    instagram_id: Optional[str] = None
    api_version: str = "v23.0"

    @staticmethod
    def load_from_env() -> "MetaConfig":
        return MetaConfig(
            app_id=os.environ["FB_APP_ID"],
            app_secret=os.environ["FB_APP_SECRET"],
            access_token=os.environ["FB_ACCESS_TOKEN"],
            ad_account_id=os.environ["FB_AD_ACCOUNT_ID"],
            page_id=os.environ["FB_PAGE_ID"],
            android_app_id=os.environ["ANDROID_APP_ID"],
            google_play_url=os.environ["GOOGLE_PLAY_URL"],
            api_version=os.environ.get("FB_API_VERSION", "v23.0"),
        )


class MetaClient:
    def __init__(self, cfg: MetaConfig):
        self._cfg = cfg
        FacebookAdsApi.init(
            app_id=cfg.app_id,
            app_secret=cfg.app_secret,
            access_token=cfg.access_token,
            api_version=cfg.api_version,
        )
        self._account = AdAccount(cfg.ad_account_id)

    def upload_image_from_bytes(self, image_bytes: bytes, filename: str = "image.png") -> str:
        import tempfile
        import os
        
        # Create a temporary file to work around SDK limitation
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_file:
            tmp_file.write(image_bytes)
            tmp_file_path = tmp_file.name
        
        try:
            img = AdImage(parent_id=self._cfg.ad_account_id)
            img[AdImage.Field.filename] = tmp_file_path
            img.remote_create()
            return img[AdImage.Field.hash]
        finally:
            # Clean up the temporary file
            if os.path.exists(tmp_file_path):
                os.unlink(tmp_file_path)

    def create_campaign(self, name: str) -> str:
        params = {
            Campaign.Field.name: name,
            Campaign.Field.objective: "OUTCOME_APP_PROMOTION",
            Campaign.Field.status: Campaign.Status.paused,
            Campaign.Field.special_ad_categories: [],
            Campaign.Field.buying_type: "AUCTION",
        }
        camp = self._account.create_campaign(params=params)
        return camp[Campaign.Field.id]

    def create_adset(
        self,
        name: str,
        campaign_id: str,
        daily_budget_minor: int,
        targeting_spec: Dict,
        optimization_goal: Optional[AdSet.OptimizationGoal] = None,
        promoted_object_overrides: Optional[Dict] = None,
        start_time_utc: Optional[str] = None,
    ) -> str:
        goal = optimization_goal or AdSet.OptimizationGoal.app_installs
        promoted_object: Dict[str, str] = {
            "application_id": self._cfg.android_app_id,
            "object_store_url": self._cfg.google_play_url,
        }
        if promoted_object_overrides:
            promoted_object.update(promoted_object_overrides)

        params = {
            AdSet.Field.name: name,
            AdSet.Field.campaign_id: campaign_id,
            AdSet.Field.daily_budget: daily_budget_minor,
            AdSet.Field.billing_event: AdSet.BillingEvent.impressions,
            # Default for image flow: optimize installs (can be overridden per-call)
            AdSet.Field.optimization_goal: goal,
            # Remove bid cap by using lowest cost without cap strategy
            AdSet.Field.bid_strategy: "LOWEST_COST_WITHOUT_CAP",
            AdSet.Field.status: AdSet.Status.paused,
            AdSet.Field.promoted_object: promoted_object,
            AdSet.Field.targeting: targeting_spec,
        }
        if start_time_utc:
            params[AdSet.Field.start_time] = start_time_utc

        # Best-effort to disable Multi-Advertiser Ads if the field is supported
        try:
            params["multi_advertiser"] = False  # Not always supported; will fallback if invalid
            aset = self._account.create_ad_set(params=params)
        except Exception as e:  # noqa: BLE001
            # Retry without multi_advertiser if it's invalid
            if "multi_advertiser" in str(e).lower():
                params.pop("multi_advertiser", None)
                aset = self._account.create_ad_set(params=params)
            else:
                raise
        return aset[AdSet.Field.id]

    def create_creative(self, name: str, image_hash: str, message: str) -> str:
        object_story_spec: Dict[str, Dict] = {
            "page_id": self._cfg.page_id,
            "link_data": {
                "message": message,
                "link": self._cfg.google_play_url,
                "image_hash": image_hash,
                "call_to_action": {
                    "type": "INSTALL_MOBILE_APP",
                    "value": {
                        "link": self._cfg.google_play_url,
                        "application": self._cfg.android_app_id,
                    },
                },
            },
        }
        # Attach Instagram account if provided (use instagram_user_id per API)
        if self._cfg.instagram_id:
            object_story_spec["instagram_user_id"] = self._cfg.instagram_id

        params = {
            AdCreative.Field.name: name,
            AdCreative.Field.object_story_spec: object_story_spec,
        }
        # Opt-out of contextual multi-advertiser (if supported)
        params["contextual_multi_ads"] = {"enroll_status": "OPT_OUT"}
        creative = self._account.create_ad_creative(params=params)
        return creative[AdCreative.Field.id]

    def upload_video_from_bytes(self, video_bytes: bytes, filename: str = "video.mp4") -> str:
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp_file:
            tmp_file.write(video_bytes)
            tmp_file_path = tmp_file.name
        try:
            video = AdVideo(parent_id=self._cfg.ad_account_id)
            # The SDK expects a local file path assigned to the filepath field
            video[AdVideo.Field.filepath] = tmp_file_path
            video.remote_create()
            return video[AdVideo.Field.id]
        finally:
            if os.path.exists(tmp_file_path):
                os.unlink(tmp_file_path)

    def extract_first_frame_and_upload(self, video_bytes: bytes) -> Optional[str]:
        """Extract the first frame of a video and upload as image to get hash.

        Tries ffmpeg pipe-first extraction (via imageio-ffmpeg bundled binary). Falls back to OpenCV if available.
        Returns image hash or None on failure.
        """
        # First attempt: ffmpeg through imageio-ffmpeg
        try:
            import tempfile
            import subprocess
            import imageio_ffmpeg  # type: ignore

            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp_v:
                tmp_v.write(video_bytes)
                tmp_v_path = tmp_v.name
            try:
                ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
                cmd = [
                    ffmpeg_exe,
                    '-y',
                    '-hide_banner',
                    '-loglevel', 'error',
                    '-i', tmp_v_path,
                    '-frames:v', '1',
                    '-f', 'image2pipe',
                    '-vcodec', 'png',
                    'pipe:1',
                ]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
                png_bytes = proc.stdout if proc.returncode == 0 and proc.stdout else None
                if png_bytes:
                    return self.upload_image_from_bytes(png_bytes, filename="thumb.png")
            finally:
                if os.path.exists(tmp_v_path):
                    os.unlink(tmp_v_path)
        except Exception:
            pass

        # Fallback: OpenCV if present
        try:
            import tempfile
            import cv2  # type: ignore

            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp_v:
                tmp_v.write(video_bytes)
                tmp_v_path = tmp_v.name
            try:
                cap = cv2.VideoCapture(tmp_v_path)
                success, frame = cap.read()
                cap.release()
                if not success or frame is None:
                    return None
                success, buf = cv2.imencode('.png', frame)
                if not success:
                    return None
                png_bytes = buf.tobytes()
                return self.upload_image_from_bytes(png_bytes, filename="thumb.png")
            finally:
                if os.path.exists(tmp_v_path):
                    os.unlink(tmp_v_path)
        except Exception:
            return None

    def get_video_thumbnail_url(self, video_id: str) -> Optional[str]:
        try:
            video = AdVideo(video_id)
            thumbs = video.get_thumbnails(fields=["uri", "is_preferred"])  # type: ignore[arg-type]
            preferred = None
            for t in thumbs:
                if t.get("is_preferred"):
                    preferred = t
                    break
            target = preferred or (thumbs[0] if thumbs else None)
            return target.get("uri") if target else None
        except Exception:
            return None

    def create_video_creative(self, name: str, video_id: str, message: str, image_url: Optional[str] = None, image_hash: Optional[str] = None) -> str:
        object_story_spec: Dict[str, Dict] = {
            "page_id": self._cfg.page_id,
            "video_data": {
                "video_id": video_id,
                "message": message,
                "call_to_action": {
                    "type": "INSTALL_MOBILE_APP",
                    "value": {
                        "link": self._cfg.google_play_url,
                        "application": self._cfg.android_app_id,
                    },
                },
            },
        }
        # Provide a thumbnail to satisfy API requirements
        if image_hash:
            object_story_spec["video_data"]["image_hash"] = image_hash
        elif image_url:
            object_story_spec["video_data"]["image_url"] = image_url
        if self._cfg.instagram_id:
            object_story_spec["instagram_user_id"] = self._cfg.instagram_id

        params = {
            AdCreative.Field.name: name,
            AdCreative.Field.object_story_spec: object_story_spec,
        }
        params["contextual_multi_ads"] = {"enroll_status": "OPT_OUT"}
        creative = self._account.create_ad_creative(params=params)
        return creative[AdCreative.Field.id]

    def create_ad(self, name: str, adset_id: str, creative_id: str) -> str:
        params = {
            Ad.Field.name: name,
            Ad.Field.adset_id: adset_id,
            Ad.Field.creative: {"creative_id": creative_id},
            Ad.Field.status: Ad.Status.paused,
        }
        ad = self._account.create_ad(params=params)
        return ad[Ad.Field.id]


