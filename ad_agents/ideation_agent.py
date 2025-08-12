from __future__ import annotations

import json
from typing import List, Optional
import os

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
    primary_text: Optional[str] = Field(
        default=None,
        description="High-converting short primary text for Meta ad (1 sentence, <= 15 words)",
    )


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
    def _generate_structured(
        self,
        app_desc: str,
        ios_url: str,
        android_url: str,
        n: int,
        platform: str,
        system_prompt_override: Optional[str] = None,
        user_prompt_override: Optional[str] = None,
    ) -> List[IdeaConcept]:
        system = (
            system_prompt_override
            if system_prompt_override is not None
            else (
                "You are a senior mobile performance creative strategist and image prompt engineer."
                " Your job is to produce testable static ad concepts for utility apps and high-quality prompts for the gpt-image-1 model."
                " Optimize for scroll-stopping visuals, message clarity, and downstream install rate."
                " Each concept must be distinct in audience, visual style, palette, and hook so we can A/B test."
            )
        )
        default_user = (
            f"App description (your only knowledge of the app): {app_desc}\n"
            f"Store URLs (context only): iOS {ios_url} | Android {android_url}\n"
            f"Target platform: {platform}.\n"
            f"Generate exactly {n} distinct ideas. Each must target a different audience persona and a different hook (social proof, urgency, benefits, problem/solution, curiosity, novelty, contrarian, FOMO).\n"
            "Creative policy & quality rules:\n"
            "- Assume the viewer does not know the brand; NEVER mention the app/brand unless explicitly present in the description above.\n"
            "- Use minimal on-image text; allowed only if <= 4 words and large, bold, highly legible.\n"
            "- Favor a single, large focal subject, clean background, and high contrast.\n"
            "- Avoid platform UI elements, device logos, competitor branding, private data, or policy-sensitive content.\n"
            "- Avoid small text, paragraphs, watermarks, or busy collages.\n"
            "- Portrait orientation 1024x1536. Keep safe margins around any text.\n"
            "Variation requirements across ideas:\n"
            "- Vary visual style (photoreal lifestyle, bold graphic poster, 3D render, UI-centric mock, conceptual illustration).\n"
            "- Vary palette (dark vs light, warm vs cool), subject (people vs object/UI), and tone (playful vs professional).\n"
            "- Each idea should test a different single insight (e.g., time saved, clarity, reliability, peace-of-mind, FOMO).\n"
            "image_prompt formatting for gpt-image-1 (produce a longer, production-ready prompt in one paragraph with labeled sections exactly as shown):\n"
            "Write as a single line with these uppercase labels and a colon after each, separated by a period and a space: \n"
            "SUBJECT & SCENARIO: <who/what; 1-2 details>. COMPOSITION: <framing, rule-of-thirds, negative space, portrait 1024x1536>. STYLE: <one of [photoreal lifestyle | bold graphic poster | 3D render | minimal flat illustration | UI-centric mock]>. LIGHTING & MOOD: <lighting and atmosphere>. COLOR PALETTE: <2-3 colors with contrast>. OPTIONAL OVERLAY TEXT: <<=4 words with placement, size, font> OR 'none'. SAFE CROP ZONES: <keep important elements away from top/bottom 10%>. NEGATIVE PROMPTS: <no logos, no platform UI, no tiny text, no watermarks, no brand names, no clutter>.\n"
            "Ensure it reads like: SUBJECT & SCENARIO: ... COMPOSITION: ... STYLE: ... LIGHTING & MOOD: ... COLOR PALETTE: ... OPTIONAL OVERLAY TEXT: ... SAFE CROP ZONES: ... NEGATIVE PROMPTS: ...\n"
            "Output fields:\n"
            "- target_audience: one sentence persona.\n"
            "- platform: 'Meta'.\n"
            "- hook: 1-2 words (e.g., 'Curiosity', 'SocialProof').\n"
            "- idea: 1-2 sentences describing what the image shows and why it works.\n"
            "- image_prompt: the full production-grade prompt per the spec above.\n"
            "- primary_text: a high-converting primary text to use in the ad (<= 15 words, avoid emojis and hashtags)."
        )
        user = user_prompt_override if user_prompt_override is not None else default_user
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

    def run(
        self,
        app_name: Optional[str],
        n: Optional[int],
        platform: Optional[str],
        prompt_file: Optional[str] = None,
        user_prompt_file: Optional[str] = None,
    ) -> int:
        cfg = self._cfg
        n_out = n or cfg.default_num_ideas
        platform_out = platform or cfg.default_platform
        system_prompt_override: Optional[str] = None
        prompt_variant: str = "default"
        user_prompt_override: Optional[str] = None
        user_prompt_variant: str = "default"
        if prompt_file:
            # Load external system prompt from file
            if not os.path.isfile(prompt_file):
                raise RuntimeError(f"Prompt file not found: {prompt_file}")
            with open(prompt_file, "r", encoding="utf-8") as f:
                system_prompt_override = f.read()
            prompt_variant = os.path.splitext(os.path.basename(prompt_file))[0]
        # Defer reading user prompt until after app metadata is loaded
        pending_user_prompt_file = user_prompt_file

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

        if pending_user_prompt_file:
            user_prompt_file = pending_user_prompt_file
            if not os.path.isfile(user_prompt_file):
                raise RuntimeError(f"User prompt file not found: {user_prompt_file}")
            with open(user_prompt_file, "r", encoding="utf-8") as f:
                tpl = f.read()
            # Allow simple template placeholders in the user prompt
            user_prompt_override = tpl.format(
                app_desc=app_desc,
                ios_url=ios_url,
                android_url=android_url,
                platform=platform_out,
                n=n_out,
            )
            user_prompt_variant = os.path.splitext(os.path.basename(user_prompt_file))[0]

        ideas = self._generate_structured(
            app_desc=app_desc,
            ios_url=ios_url,
            android_url=android_url,
            n=n_out,
            platform=platform_out,
            system_prompt_override=system_prompt_override,
            user_prompt_override=user_prompt_override,
        )

        if not ideas:
            console.print("[red]No ideas generated[/red]")
            return 0

        # Convert to dicts and append to sheet
        idea_dicts = []
        for i in ideas:
            data = i.model_dump()
            data["prompt_variant"] = prompt_variant
            data["user_prompt_variant"] = user_prompt_variant
            idea_dicts.append(data)
        _, count = self._sheets.append_ideas(app_name=app_name, ideas=idea_dicts, platform=platform_out)
        console.print(f"[green]Appended {count} ideas for {app_name}[/green]")
        return count

