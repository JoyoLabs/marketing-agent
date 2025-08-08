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
    def _generate_structured(self, app_desc: str, ios_url: str, android_url: str, n: int, platform: str) -> List[IdeaConcept]:
        system = (
            "You are a senior mobile performance creative strategist and image prompt engineer."
            " Your job is to produce testable static ad concepts for utility apps and high-quality prompts for the gpt-image-1 model."
            " Optimize for scroll-stopping visuals, message clarity, and downstream install rate."
            " Each concept must be distinct in audience, visual style, palette, and hook so we can A/B test."
        )
        user = (
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
            "image_prompt specification for gpt-image-1 (produce a longer, production-ready prompt):\n"
            "- SUBJECT & SCENARIO: who/what is shown; 1-2 concrete details.\n"
            "- COMPOSITION: framing (e.g., close-up hero), rule-of-thirds, negative space for optional short phrase, portrait 1024x1536.\n"
            "- STYLE: one of [photoreal lifestyle | bold graphic poster | 3D render | minimal flat illustration | UI-centric mock].\n"
            "- LIGHTING & MOOD: e.g., soft natural daylight, dramatic rim light, cozy warm.\n"
            "- COLOR PALETTE: 2-3 colors with strong contrast; optionally include hex-like descriptors (e.g., deep navy, bright coral).\n"
            "- OPTIONAL OVERLAY TEXT: <= 4 words OR 'none'; include placement (e.g., top-left), size (large), and font vibe (bold modern sans-serif).\n"
            "- SAFE CROP ZONES: keep important subjects and any overlay text outside top 10% and bottom 10% of the canvas (some placements crop).\n"
            "- NEGATIVE PROMPTS: no logos, no platform UI, no tiny text, no watermarks, no brand or app names, no clutter.\n"
            "- OUTPUT as a coherent single paragraph (no lists), suitable to paste into gpt-image-1.\n"
            "Output fields:\n"
            "- target_audience: one sentence persona.\n"
            "- platform: 'Meta'.\n"
            "- hook: 1-2 words (e.g., 'Curiosity', 'SocialProof').\n"
            "- idea: 1-2 sentences describing what the image shows and why it works.\n"
            "- image_prompt: the full production-grade prompt per the spec above.\n"
            "- primary_text: a high-converting primary text to use in the ad (<= 15 words, avoid emojis and hashtags)."
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

