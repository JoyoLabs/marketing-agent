Meta Marketing API: end-to-end playbook for our PH + Android test campaigns (Python, Cursor agent mode)
Goal: from a single function call, create a paused test campaign on Meta that targets Philippines and Android, using one static image we already generated, then return all IDs and write them back to Google Sheets / Drive.

This doc is written for Cursor’s agent mode so it doesn’t hallucinate. It spells out the exact objects, fields, and the order of operations, with copy-pasteable Python using the facebook-business SDK.

0) Prereqs & one-time setup (do these first)
Access + permissions

Create a Meta app in developers portal and add the Marketing API product.

Use a System User under Business Manager, assign the ad account & Page, and generate a system user access token with at least ads_management (and pages_manage_ads if you’ll attach a Page). Your app must have Standard Access to create a system user token. 

Ad account & Page

Have the destination ad account ID (format act_<id>), funding source set up, and a Facebook Page that will own the post in the creative. Object story specs require a Page. 

App promotion plumbing

Your Android app must exist on Google Play and be connected in Events Manager via MMP (AppsFlyer) or SDK/CAA so Meta can attribute installs. (We already send AppsFlyer data to Meta; that’s fine for basic app promotion.)

Images

You’ll upload images to the ad account via AdImage to get an image_hash (required for stable creatives). 
Facebook Geliştiricileri

Special Ad Category (SAC)

Every campaign creation call must include special_ad_categories. For our utility app, use []. (If you ever run Housing/Employment/Credit/Politics, set the right one.) 
Facebook Geliştiricileri
+1

SDK + versions

Python package: facebook-business (Meta Business SDK).

You can pin API version in init: FacebookAdsApi.init(..., api_version='v23.0'). 
Facebook Geliştiricileri
Stack Overflow

1) High-level flow the agent must follow
Init SDK (set token, account)

Upload image → get image_hash

Create Campaign (objective = OUTCOME_APP_PROMOTION, status=PAUSED, special_ad_categories=[]) 
Facebook Geliştiricileri

Create Ad Set with:

promoted_object → {application_id, object_store_url} (Google Play URL)

targeting → PH + Android (see exact JSON below)

daily_budget (minor currency units; e.g., 500 = $5 if account is USD)

status=PAUSED

placements (FB + IG + optionally Audience Network)
Budget/amount fields use the smallest currency unit. 
Facebook Geliştiricileri

Create Ad Creative with object_story_spec (Page + link_data + image_hash + CTA INSTALL_MOBILE_APP). 
Facebook Geliştiricileri
+1

Create Ad attached to the ad set (status=PAUSED).

(Optional) Generate preview for QA. 
Facebook Geliştiricileri

(Optional) Delivery estimate to sanity-check audience size. 
Facebook Geliştiricileri

Write back all IDs to Google Sheets; store creative preview URL; keep image bytes & meta in Drive.

Everything stays paused until we manually review in Ads Manager.

2) Targeting spec for Philippines + Android (copy this)
json
Kopyala
Düzenle
{
  "geo_locations": { "countries": ["PH"] },
  "age_min": 18,
  "device_platforms": ["mobile"],
  "user_os": ["Android"],
  "publisher_platforms": ["facebook", "instagram", "audience_network"],
  "facebook_positions": ["feed", "marketplace", "video_feeds", "story", "reels"],
  "instagram_positions": ["feed", "explore", "story", "reels"]
}
Notes:

user_os is required for mobile app ads; use ["Android"]. 
Facebook Geliştiricileri
+1

Placement fields are under placement targeting. Adjust to your needs. 
Facebook Geliştiricileri

3) Minimal golden-path Python (SDK) the Cursor agent should implement
Assumes you’ve set env vars: FB_APP_ID, FB_APP_SECRET, FB_ACCESS_TOKEN, FB_AD_ACCOUNT_ID (like act_123...), FB_PAGE_ID, ANDROID_APP_ID (numerical Meta app id), GOOGLE_PLAY_URL (e.g., https://play.google.com/store/apps/details?id=com.your.app)

python
Kopyala
Düzenle
import os, json, time
from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.campaign import Campaign
from facebook_business.adobjects.adset import AdSet
from facebook_business.adobjects.adimage import AdImage
from facebook_business.adobjects.adcreative import AdCreative
from facebook_business.adobjects.ad import Ad
from facebook_business.exceptions import FacebookRequestError

# ---- 0) Init ----
APP_ID = os.environ["FB_APP_ID"]
APP_SECRET = os.environ["FB_APP_SECRET"]
ACCESS_TOKEN = os.environ["FB_ACCESS_TOKEN"]
AD_ACCOUNT_ID = os.environ["FB_AD_ACCOUNT_ID"]   # "act_..."
PAGE_ID = os.environ["FB_PAGE_ID"]
ANDROID_APP_ID = os.environ["ANDROID_APP_ID"]    # Meta App ID, not package name
GOOGLE_PLAY_URL = os.environ["GOOGLE_PLAY_URL"]

FacebookAdsApi.init(app_id=APP_ID, app_secret=APP_SECRET, access_token=ACCESS_TOKEN, api_version="v23.0")

account = AdAccount(AD_ACCOUNT_ID)

def upload_image_from_bytes(image_bytes, filename_hint="creative.jpg"):
    img = AdImage(parent_id=AD_ACCOUNT_ID)
    img[AdImage.Field.bytes] = image_bytes
    img.remote_create()
    return img[AdImage.Field.hash]   # image_hash

def create_campaign(name):
    params = {
        Campaign.Field.name: name,
        Campaign.Field.objective: "OUTCOME_APP_PROMOTION",
        Campaign.Field.status: Campaign.Status.paused,
        Campaign.Field.special_ad_categories: [],  # REQUIRED; empty for non-SAC
        Campaign.Field.buying_type: "AUCTION"
    }
    camp = account.create_campaign(params=params)
    return camp[Campaign.Field.id]

def create_adset(name, campaign_id, daily_budget_minor, targeting_spec):
    params = {
        AdSet.Field.name: name,
        AdSet.Field.campaign_id: campaign_id,
        AdSet.Field.daily_budget: daily_budget_minor,   # minor units
        AdSet.Field.billing_event: AdSet.BillingEvent.impressions,
        AdSet.Field.optimization_goal: AdSet.OptimizationGoal.app_install,
        AdSet.Field.status: AdSet.Status.paused,
        AdSet.Field.promoted_object: {
            "application_id": ANDROID_APP_ID,
            "object_store_url": GOOGLE_PLAY_URL
        },
        AdSet.Field.targeting: targeting_spec,
        # Optional: schedule
        # AdSet.Field.start_time: (int(time.time()) + 3600),
    }
    aset = account.create_ad_set(params=params)
    return aset[AdSet.Field.id]

def create_creative(name, page_id, image_hash, link_url, message, cta_type="INSTALL_MOBILE_APP"):
    params = {
        AdCreative.Field.name: name,
        AdCreative.Field.object_story_spec: {
            "page_id": page_id,
            "link_data": {
                "message": message,
                "link": link_url,
                "image_hash": image_hash,
                "call_to_action": {
                    "type": cta_type,
                    "value": {
                        "link": link_url,
                        "application": ANDROID_APP_ID
                    }
                }
            }
        }
    }
    creative = account.create_ad_creative(params=params)
    return creative[AdCreative.Field.id]

def create_ad(name, adset_id, creative_id):
    params = {
        Ad.Field.name: name,
        Ad.Field.adset_id: adset_id,
        Ad.Field.creative: { "creative_id": creative_id },
        Ad.Field.status: Ad.Status.paused
    }
    ad = account.create_ad(params=params)
    return ad[Ad.Field.id]
Why these fields?

AdImage → image_hash is the canonical way to reference the asset in creatives. 
Facebook Geliştiricileri

Campaign.objective="OUTCOME_APP_PROMOTION" is the current ODAX objective for app ads.

special_ad_categories=[] is required on creation (even if empty). 
Facebook Geliştiricileri

AdSet.promoted_object must include the Meta Android application_id and the Google Play object_store_url. (Standard app ads requirement.) 
Facebook Geliştiricileri

object_story_spec with page_id + link_data + image_hash + CTA INSTALL_MOBILE_APP is the supported pattern for link-style app creatives. 
Facebook Geliştiricileri
+1

daily_budget is in minor currency units (e.g., $5 → 500). 
Facebook Geliştiricileri

4) Exact targeting JSON the function should use
python
Kopyala
Düzenle
TARGETING_PH_ANDROID = {
  "geo_locations": { "countries": ["PH"] },
  "age_min": 18,
  "device_platforms": ["mobile"],
  "user_os": ["Android"],
  "publisher_platforms": ["facebook", "instagram", "audience_network"],
  "facebook_positions": ["feed", "marketplace", "video_feeds", "story", "reels"],
  "instagram_positions": ["feed", "explore", "story", "reels"]
}
This satisfies Meta’s mobile app ads requirement to include user_os, and constrains delivery to mobile placements. 
Facebook Geliştiricileri
+1

5) Putting it together (one convenience wrapper)
python
Kopyala
Düzenle
def create_paused_ph_android_test(name_prefix, image_bytes, link_text="Try it free"):
    image_hash = upload_image_from_bytes(image_bytes)
    campaign_id = create_campaign(f"{name_prefix} | Camp")
    adset_id = create_adset(
        name=f"{name_prefix} | ASet PH-Android",
        campaign_id=campaign_id,
        daily_budget_minor=300,  # e.g., $3 if account is USD
        targeting_spec=TARGETING_PH_ANDROID
    )
    creative_id = create_creative(
        name=f"{name_prefix} | Creative",
        page_id=PAGE_ID,
        image_hash=image_hash,
        link_url=GOOGLE_PLAY_URL,
        message=link_text
    )
    ad_id = create_ad(f"{name_prefix} | Ad 1", adset_id, creative_id)
    return {
        "campaign_id": campaign_id,
        "adset_id": adset_id,
        "creative_id": creative_id,
        "ad_id": ad_id,
        "image_hash": image_hash
    }
6) Optional but recommended: preview & delivery checks
Preview (so you can paste an iframe or URL into a Sheet row for QA): use the account’s generatepreviews edge with your creative & an ad_format like MOBILE_FEED_STANDARD or IG formats. 
Facebook Geliştiricileri

Delivery estimate: /act_{id}/delivery_estimate with your targeting to sanity-check reach. 
Facebook Geliştiricileri

7) Cursor agent tasks (step-by-step)
Task 1 — SDK & config

Add facebook-business==<latest> to requirements.

Read all Meta credentials from environment variables.

Implement meta_client.py exposing the functions above.

Task 2 — Image ingestion

Get image bytes from Drive (the upstream step). Pass the bytes to upload_image_from_bytes(); don’t rely on a public URL.

Task 3 — Campaign flow

Create objects in the order: Campaign → AdSet → AdCreative → Ad. Keep everything PAUSED.

Write all IDs + timestamps + preview URL to the current row in Google Sheets (columns: idea_id, image_drive_file_id, campaign_id, adset_id, creative_id, ad_id, preview_url, status).

Task 4 — Idempotency

Before creating, search by name under the account (campaigns/adsets/ads) and skip or suffix -v2 if a same-name item exists (avoid duplicates when rerunning).

Task 5 — Error handling

Catch FacebookRequestError: bubble up api_error_subcode, error_user_title, error_user_msg.

Common blockers to guard for:

Missing/invalid special_ad_categories (fix: send []). 
Facebook Geliştiricileri

Invalid promoted_object (app id / store URL mismatch).

Token missing ads_management or Page permissions (use Access Token Debugger / Graph API Explorer). 
Stack Overflow

Task 6 — Optional QA

Generate ad preview and store the URL/HTML into Sheets for reviewer.

Call delivery_estimate; store reach bucket in Sheets.

8) Typical values for our PH Android tests
Budget: start tiny (e.g., $2–$5 daily). Remember daily_budget is minor units (USD $5 → 500). 
Facebook Geliştiricileri

Placements: start with FB + IG feed/story/reels; you can include Audience Network for scale.

Optimization: app_install is fine to start (you can iterate to AEO once events/AF mapping are reliable).

9) Useful reference endpoints (official docs)
Marketing API overview & SDKs. 
Facebook Geliştiricileri
+1

AdImage (upload → image_hash). 
Facebook Geliştiricileri

AdCreative + object_story_spec (link/photo/video data). 
Facebook Geliştiricileri
+1

Call-to-action types (INSTALL_MOBILE_APP, etc.). 
Facebook Geliştiricileri

Campaign creation & objectives (ODAX). 
Facebook Geliştiricileri

AdSet & targeting + user_os for mobile app ads. 
Facebook Geliştiricileri
+1

Placement targeting (publisher/positions). 
Facebook Geliştiricileri

Special Ad Categories (required field). 
Facebook Geliştiricileri
+1

Budgets & minor currency units. 
Facebook Geliştiricileri

Previews. 
Facebook Geliştiricileri

Delivery estimate. 
Facebook Geliştiricileri

System users & tokens (Standard Access needed). 
Facebook Geliştiricileri

Postman collection (sanity-check endpoints quickly). 
Postman

10) Sanity checklist before turning ads ON
Campaign has special_ad_categories=[]. 
Facebook Geliştiricileri

Ad set has promoted_object.application_id and correct object_store_url (Google Play). 
Facebook Geliştiricileri

Targeting includes user_os: ["Android"] and device_platforms: ["mobile"]. 
Facebook Geliştiricileri

Creative has page_id, image_hash, call_to_action.type="INSTALL_MOBILE_APP". 
Facebook Geliştiricileri
+1

Daily budget is in minor units; not below account minimum. 
Facebook Geliştiricileri

Preview renders; delivery estimate is non-zero. 
Facebook Geliştiricileri
+1

11) Common errors & fixes (fast)
(#100) Invalid parameter on campaign create → you probably omitted special_ad_categories. Send []. 
Facebook Geliştiricileri

promoted_object errors → verify the Meta app id (not package name) and the Play URL; ensure the app is connected in Events Manager. 
Facebook Geliştiricileri

Permission / token errors → regenerate a system user token with ads_management and asset assignments; confirm in token debugger. 
Facebook Geliştiricileri
Stack Overflow

No preview → ensure creative has a Page and valid media (image_hash); try a different ad_format. 
Facebook Geliştiricileri

12) Nice-to-have extensions (after the happy path)
Add a generate_previews step and paste the preview iframe HTML into Sheets for human QA. 
Facebook Geliştiricileri

Add /delivery_estimate check; log the reach bucket to Sheets. 
Facebook Geliştiricileri

Add a “promote” function that flips status=ACTIVE on ad set + ad once QA column is set to ✅.

Wrap calls with exponential backoff on FacebookRequestError and log error_subcode.


Min Os Version 10.0
Placement Full Açık
Instagram Account craftnote_ai
Multi-advertiser OFF
Revise the text
Ad Naming: Add CN_AI_ to the start and aspect ratio to the end
Instead of MMP write IPM and move it before Android



