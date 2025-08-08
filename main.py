from __future__ import annotations

import sys
import logging
from typing import Optional

import typer
from rich.console import Console

from ad_agents.config import AppConfig
from ad_agents.ideation_agent import IdeationAgent
from ad_agents.image_agent import ImageGenerationAgent
from ad_agents.campaign_agent import CampaignAgent


app = typer.Typer(help="Creative Agent System")
console = Console()

# Enable debug logging
logging.basicConfig(level=logging.DEBUG)


@app.command()
def ideate(
    app_name: Optional[str] = typer.Option(
        None, help="App name as in the App List sheet (AppName column)"
    ),
    n: Optional[int] = typer.Option(None, help="Number of ideas to generate"),
    platform: Optional[str] = typer.Option(None, help="Ad platform label (e.g., Meta)"),
):
    cfg = AppConfig.load_from_env()
    agent = IdeationAgent(cfg)
    count = agent.run(app_name=app_name, n=n, platform=platform)
    console.print(f"Generated {count} ideas")


@app.command("generate-images")
def generate_images():
    cfg = AppConfig.load_from_env()
    agent = ImageGenerationAgent(cfg)
    count = agent.run()
    console.print(f"Generated & uploaded {count} images")
@app.command("create-campaigns")
def create_campaigns(
    n: int = typer.Option(1, help="Number of rows to process"),
    budget_minor: int = typer.Option(300, help="Daily budget in minor units (e.g., 500 = $5)"),
    app_name: Optional[str] = typer.Option(None, help="Only process rows for this App_Name"),
):
    cfg = AppConfig.load_from_env()
    created = CampaignAgent(cfg).run(n=n, budget_minor=budget_minor, app_name_filter=app_name)
    console.print(f"Created {created} ads")



@app.command()
def run(
    app_name: Optional[str] = typer.Option(None, help="App name for ideation"),
    n: Optional[int] = typer.Option(None, help="Number of ideas to generate"),
    platform: Optional[str] = typer.Option(None, help="Ad platform label (e.g., Meta)"),
):
    """Run ideation then image generation."""
    cfg = AppConfig.load_from_env()
    IdeationAgent(cfg).run(app_name=app_name, n=n, platform=platform)
    ImageGenerationAgent(cfg).run()


@app.command("full-run")
def full_run(
    app_name: str = typer.Option(..., help="App name as in App List sheet (AppName)"),
    n: int = typer.Option(5, help="Number of ideas/images to create"),
    budget_minor: int = typer.Option(300, help="Daily budget in minor units (e.g., 500 = $5)"),
):
    """Create one campaign with n images for a given app: ideate -> generate-images -> create-campaigns."""
    cfg = AppConfig.load_from_env()
    console.print(f"[blue]Ideating {n} ideas for {app_name}...[/blue]")
    IdeationAgent(cfg).run(app_name=app_name, n=n, platform="Meta")
    console.print("[blue]Generating images...[/blue]")
    ImageGenerationAgent(cfg).run()
    console.print("[blue]Creating campaign...[/blue]")
    CampaignAgent(cfg).run(n=n, budget_minor=budget_minor, app_name_filter=app_name)


if __name__ == "__main__":
    try:
        app()
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)

