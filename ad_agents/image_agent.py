from __future__ import annotations

import base64
import io
from typing import List

from openai import OpenAI
from rich.console import Console
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import AppConfig
from .google_drive import DriveClient
from .google_sheets import SheetsClient


console = Console()


class ImageGenerationAgent:
    def __init__(self, cfg: AppConfig):
        if not cfg.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required")
        self._cfg = cfg
        self._client = OpenAI(api_key=cfg.openai_api_key)
        self._sheets = SheetsClient(cfg)
        self._drive = DriveClient(cfg)

    @retry(wait=wait_exponential(multiplier=1, min=1, max=20), stop=stop_after_attempt(5))
    def _generate_image_png(self, prompt: str) -> bytes:
        img = self._client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            n=1,
            size="1024x1536",
        )
        b64 = img.data[0].b64_json
        return base64.b64decode(b64)

    def run(self) -> int:
        rows = self._sheets.list_ideated()
        if not rows:
            console.print("[yellow]No ideated rows to generate images for[/yellow]")
            return 0
        completed = 0
        for row in rows:
            row_index = row["_row_index"]
            prompt = row.get("Image_Prompt", "")
            idea_id = row.get("ID", "")
            hook = row.get("Hook", "").replace(" ", "").replace("-", "").replace("_", "")
            if not prompt:
                continue
            try:
                png_bytes = self._generate_image_png(prompt)
                # Naming convention: Type_Id_Hook
                filename = f"Image_{idea_id}_{hook}.png"
                link = self._drive.upload_png_bytes(png_bytes, filename)
                self._sheets.update_row(
                    row_index,
                    {"Image_URL": link, "Status": "Uploaded"},
                )
                completed += 1
                console.print(f"[green]Uploaded image for row {row_index}: {link}[/green]")
            except Exception as e:  # noqa: BLE001
                console.print(f"[red]Failed row {row_index}: {e}[/red]")
        return completed

