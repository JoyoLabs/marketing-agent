## Creative Agent System

Production-grade agents to generate ad ideas, images, and Meta campaigns for utility mobile apps.

### Components
- Ideation Agent: Generates structured ideas with short hooks and image prompts.
- Image Agent: Generates 1024x1536 images, uploads to Google Drive (Shared Drive), writes URL to Ideas sheet.
- Campaign Agent: Creates Meta campaign/ad set, uploads ad images, creates creatives and ads.

### Tech stack
- OpenAI `gpt-4o` structured output (with Pydantic schema) and `gpt-image-1` for images
- Google Sheets (`gspread`) and Drive (`google-api-python-client`)
- Meta Marketing API (`facebook-business` SDK)
- CLI via `typer`, logging via `rich`

---

## Setup
1) Python 3.10+ and venv

```bash
python -m venv .venv
. .venv/Scripts/activate  # PowerShell: . .venv/Scripts/Activate.ps1
pip install -r requirements.txt
```

2) .env (copy from template) and credentials

```bash
copy env.template .env
```

Required env values:
- OPENAI_API_KEY
- GOOGLE_SERVICE_ACCOUNT_JSON_PATH or GOOGLE_SERVICE_ACCOUNT_JSON
- APP_LIST_SHEET_ID, IDEAS_SHEET_ID, DRIVE_FOLDER_ID, CAMPAIGN_CONFIG_SHEET_ID
- FB_APP_ID, FB_APP_SECRET, FB_ACCESS_TOKEN, FB_API_VERSION (e.g., v23.0)

3) Share to Service Account (must be Editor)
- App List sheet, Ideas sheet, Campaign Config sheet
- Drive folder must be in a Shared Drive (service accounts have no personal storage quota)

---

## Sheets Schemas

App List (Sheet1)
- AppName, Description, iOS_URL, Android_URL

Ideas (Sheet1)
- ID, Timestamp, Status, App_Name, Target_Audience, Platform, Hook, Idea, Image_Prompt, Image_URL, campaign_id, adset_id, creative_id, ad_id, image_hash

Campaign Config (Sheet1)
- AppName, Network, CampaignType, DataSource, Targeting, Daily_Budget, Geo, Platform, Meta_App_ID, GOOGLE_PLAY_URL, FB_PAGE_ID, AD_ACCOUNT_ID, INSTAGRAM_ID, Latest_Campaign_ID

Notes
- Daily_Budget is in dollars; the system converts to minor units internally.
- Latest_Campaign_ID is prefixed to campaign names and auto-incremented after a successful create.

---

## Usage

Generate ideas
```bash
python main.py ideate --app-name "Your App Name" --n 5 --platform Meta
```

Generate images for all Ideated rows
```bash
python main.py generate-images
```

Create campaigns (single campaign per app, multiple ads)
```bash
# Process first N uploaded rows (any app)
python main.py create-campaigns --n 5

# Only for a specific app
python main.py create-campaigns --n 5 --app-name "Your App Name"
```

End-to-end (one campaign with n images)
```bash
python main.py full-run --app-name "Your App Name" --n 5 --budget-minor 500
```

---

## Behaviors & Conventions

Ideation
- Uses `gpt-4o` with strict Pydantic schema (target_audience, platform, hook, idea, image_prompt)
- Prompt tuned to: no brand mentions unless in description, minimal on-image text (<= 4 words), mobile portrait 1024x1536

Images
- Uses `gpt-image-1` (1024x1536)
- File naming on Drive: `Image_<ID>_<Hook>.png`

Meta Campaigns
- Single campaign per app per run; if an existing `campaign_id/adset_id` is already present for the app in Ideas, new ads are attached to that campaign.
- Campaign naming: `<Latest_Campaign_ID>_<AppName>_<Network>_<Platform>_<DataSource>_<Geo>_<Targeting>_<CampaignType>_<DDMMYY>`
- Targeting:
  - device_platforms: mobile
  - user_os: ["Android_ver_10.0_and_above"]
  - publisher_platforms: facebook, instagram, audience_network, messenger (automatic placements)
- Bidding: `LOWEST_COST_WITHOUT_CAP` (no bid cap)
- All created objects start in PAUSED state
- Creative options:
  - instagram_user_id is set from INSTAGRAM_ID
  - contextual_multi_ads set to `{ "enroll_status": "OPT_OUT" }`
- Ad/Creative name: `CN_AI_<DriveImageName>` with numeric IDs zero-padded to 4, app token removed

---

## Quotas & Performance
- The agent minimizes Google Sheets reads with worksheet/header caching, row-wise reads, and batched writes.
- For large batches, prefer `--app-name` to avoid scanning unrelated rows.

---

## Troubleshooting
- 403 on Google Sheets: share sheets/drive folder with the service account email as Editor.
- Drive upload 403 (quota): ensure folder is in a Shared Drive.
- Meta errors (placements/optimization): the SDK/API can be strict; current config uses automatic placements and `app_installs` optimization.

---

## Development
- CLI commands live in `main.py`
- Agents in `ad_agents/` (ideation, image, campaign)
- Config via `.env` and `ad_agents/config.py`