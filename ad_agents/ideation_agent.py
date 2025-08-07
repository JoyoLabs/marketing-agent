from __future__ import annotations

import json
from typing import List, Optional

from openai import OpenAI
from pydantic import BaseModel, Field
from rich.console import Console
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import AppConfig
from .google_sheets import SheetsClient


console = Console()


class IdeaConcept(BaseModel):
    target_audience: str = Field(..., description="Who the ad is for")
    platform: str = Field(..., description="Ad platform, e.g., Meta")
    hook: str = Field(..., description="Short 1-2 word hook describing the unique point of the creative")
    idea: str = Field(..., description="Concise ad concept copy, 1-2 sentences")
    image_prompt: str = Field(..., description="Prompt for gpt-image-1 to create the visual")


class IdeasEnvelope(BaseModel):
    ideas: List[IdeaConcept]


class IdeationAgent:
    def __init__(self, cfg: AppConfig):
        self._cfg = cfg
        if not cfg.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required")
        self._client = OpenAI(api_key=cfg.openai_api_key)
        self._sheets = SheetsClient(cfg)

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        t = text.strip()
        if t.startswith("```"):
            # remove first fence line
            t = t.split("\n", 1)[-1]
            # remove closing fence if present
            if t.endswith("```"):
                t = t.rsplit("\n", 1)[0]
        return t.strip()

    @retry(wait=wait_exponential(multiplier=1, min=1, max=20), stop=stop_after_attempt(5))
    def _generate_structured(self, app_desc: str, ios_url: str, android_url: str, n: int, platform: str) -> List[IdeaConcept]:
        system = (
            "You are an expert direct-response creative strategist. Generate high-performing static ad concepts that target distinct audiences and hooks."
        )
        user = (
            f"App description: {app_desc}\n"
            f"iOS URL: {ios_url}\n"
            f"Android URL: {android_url}\n"
            f"Target platform: {platform}.\n"
            f"Generate exactly {n} distinct ideas. Each should focus on a different audience and a different hook (social proof, urgency, benefits, problem/solution, curiosity, contrarian, etc.)."
        )
        console.print("[blue]Generating ad ideas with OpenAI (structured output)...[/blue]")
        # Primary: Structured output via Responses API (enveloped list)
        try:
            response = self._client.responses.parse(
                model="gpt-4o",
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                text_format=IdeasEnvelope,
            )
            envelope: IdeasEnvelope = response.output_parsed  # type: ignore[assignment]
            return envelope.ideas
        except Exception as e:
            console.print(f"[yellow]Structured output parse failed, falling back to JSON: {e}[/yellow]")
            # Fallback: ask for JSON object with 'ideas' array
            completion = self._client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": user
                        + "\nRespond ONLY as a JSON object with key 'ideas' whose value is an array of objects {target_audience, platform, hook, idea, image_prompt}. No extra text, no code fences.",
                    },
                ],
                temperature=0.2,
            )
            text = completion.choices[0].message.content or "{}"
            raw = self._strip_code_fences(text)
            try:
                data = json.loads(raw)
            except Exception:
                # Try to salvage JSON array
                data = {}
            items = []
            if isinstance(data, dict) and isinstance(data.get("ideas"), list):
                items = data["ideas"]
            elif isinstance(data, list):
                items = data
            result: List[IdeaConcept] = []
            for item in items:
                try:
                    result.append(IdeaConcept(**item))
                except Exception:
                    continue
            return result

    def run(self, app_name: Optional[str], n: Optional[int], platform: Optional[str]) -> int:
        cfg = self._cfg
        n_out = n or cfg.default_num_ideas
        platform_out = platform or cfg.default_platform

        console.print("[blue]Loading app list from Google Sheets...[/blue]")
        app_row = None
        if app_name:
            app_row = self._sheets.get_app_by_name(app_name)
            if not app_row:
                raise RuntimeError(f"App '{app_name}' not found in app list sheet")
        else:
            apps = self._sheets.list_apps()
            if not apps:
                raise RuntimeError("App list sheet is empty")
            app_row = apps[0]
            app_name = app_row.get("AppName", "Unknown App")
            console.print(f"[yellow]App name not provided. Defaulting to first app: {app_name}[/yellow]")

        app_desc = app_row.get("Description", "")
        ios_url = app_row.get("iOS_URL", "")
        android_url = app_row.get("Android_URL", "")

        ideas = self._generate_structured(
            app_desc=app_desc,
            ios_url=ios_url,
            android_url=android_url,
            n=n_out,
            platform=platform_out,
        )

        if not ideas:
            console.print("[red]No ideas generated[/red]")
            return 0

        # Convert to dicts and append to sheet
        idea_dicts = [i.model_dump() for i in ideas]
        _, count = self._sheets.append_ideas(app_name=app_name, ideas=idea_dicts, platform=platform_out)
        console.print(f"[green]Appended {count} ideas for {app_name}[/green]")
        return count

