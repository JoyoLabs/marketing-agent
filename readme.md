We are a mobile app company and we promote mostly utility apps. I want to develop a system of agents that does the following:

1. Static Ad Ideation Agent
It first reads the following sheet which contains our app list and information (https://docs.google.com/spreadsheets/d/13QQF7vVGiSr07U1Jcj-fR3plPXyX0D-0iVc0Ei8YjxI)
This sheet has 4 columns AppName, Description, iOS_URL, Android_URL
It takes the Description of the app we want to make static ads for.

Then, it reads the following google sheet (https://docs.google.com/spreadsheets/d/1JaVenG582kvZwaS3noxHGwbGu1-mNtv_mwvaljZUMJo/)
Sheet has the following columns (ID	Timestamp	Status	App_Name	Target_Audience	Platform	Idea	Image_Prompt	Image_URL) 
At every run we should generate a given number of ideas (Default n = 10)
ID starts from 1 and is a counter
Status might be one of the following: Ideated, Rejected, Generated, Uploaded, Tested
Platform is the target ad platform (right now we are targetting Meta)
Other fields up to the Image_Prompt will be filled by the ad ideation agent.

This agent should generate a list of new ideas by using openai's gpt-5-mini structured output mode and write the responses to the sheet. We will be passing the image prompts to the gpt-image-1 model and these prompts should try to attract different customer audiences and have different hooks. Our goal is to find winner creatives to reduce our overall acquisition cost and have high returns. 

2. Image generation agent
This agent reads the ideas sheet and generates images for all the ideas that have their status set to ideated.
It uses the gpt-image-1 model, the sample usage is as follows: 
import base64
from openai import OpenAI
client = OpenAI()

img = client.images.generate(
    model="gpt-image-1",
    prompt="A cute baby sea otter",
    n=1,
    size="1024x1024"
)

image_bytes = base64.b64decode(img.data[0].b64_json)
with open("output.png", "wb") as f:
    f.write(image_bytes)


We want the size to be 1024x1536

It should save the generated images to the following google drive folder (https://drive.google.com/drive/u/0/folders/1aAKwxkSl3z_tcu_AgXB4WJSAL50BKWML)
And write the url of the image to the sheet. 

I have put our openai sdk key into the .env file. 


## Implementation

This repository now contains a production-grade Python agent system with two CLI commands:

- `ideate`: Generates ad ideas into the Ideas sheet using OpenAI structured output.
- `generate-images`: Reads rows with `Status = Ideated`, generates images with `gpt-image-1`, uploads to Google Drive, and writes the shareable URL back, updating `Status = Uploaded`.

You can also run `run` to do both sequentially.

### Tech stack
- OpenAI Responses API with structured output on `gpt-5-mini`
- OpenAI Images API `gpt-image-1`
- Google Sheets via `gspread`
- Google Drive via `google-api-python-client`
- CLI via `typer` and logs via `rich`

### Setup
1) Install Python 3.10+ and create a virtual environment.

```bash
python -m venv .venv
. .venv/Scripts/activate  # Windows PowerShell: . .venv/Scripts/Activate.ps1
pip install -r requirements.txt
```

2) Create `.env` from `env.template` and fill in values.

```bash
copy env.template .env  # Windows
```

Required values:
- `OPENAI_API_KEY`
- Provide Google Service Account credentials via one of:
  - `GOOGLE_SERVICE_ACCOUNT_JSON_PATH` pointing to the downloaded JSON file
  - or `GOOGLE_SERVICE_ACCOUNT_JSON` containing the raw JSON

3) Share access with your Service Account:
- Open both Google Sheets and click Share → add the service account email as Editor
- Open the target Google Drive folder and share to the same service account as Editor

4) Verify the sheet headers (Ideas sheet) are exactly:
`ID, Timestamp, Status, App_Name, Target_Audience, Platform, Hook, Idea, Image_Prompt, Image_URL`

The system will create or overwrite the first header row to match this schema if needed.

### Usage

Generate ideas for a specific app (by AppName in the App List sheet):

```bash
python main.py ideate --app-name "Your App Name" --n 10 --platform Meta
```

Generate images for all `Ideated` rows:

```bash
python main.py generate-images
```

Run both sequentially:

```bash
python main.py run --app-name "Your App Name" --n 10 --platform Meta
```

### Notes
- Image size is `1024x1536` as requested.
- Status progression used: `Ideated` → `Uploaded` (after image is generated and link written).
- IDs are auto-incremented starting from 1. Timestamps are in UTC ISO-8601.
- The ideation agent ensures audiences and hooks are diverse.