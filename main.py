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
from ad_agents.video_campaign_agent import VideoCampaignAgent
import glob
import os


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
    prompt_file: Optional[str] = typer.Option(None, help="Path to a system prompt file for ideation experiments"),
    user_prompt_file: Optional[str] = typer.Option(None, help="Path to a user prompt template file (supports {app_desc},{ios_url},{android_url},{platform},{n})"),
):
    cfg = AppConfig.load_from_env()
    agent = IdeationAgent(cfg)
    count = agent.run(app_name=app_name, n=n, platform=platform, prompt_file=prompt_file, user_prompt_file=user_prompt_file)
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


@app.command("scan-videos")
def scan_videos(
    app_name: Optional[str] = typer.Option(None, help="Default App_Name to assign to discovered videos"),
    campaign_type: Optional[str] = typer.Option(None, help="Default CampaignType to assign to discovered videos"),
):
    cfg = AppConfig.load_from_env()
    inserted = VideoCampaignAgent(cfg).scan_videos(default_app_name=app_name, default_campaign_type=campaign_type)
    console.print(f"Inserted {inserted} new video rows")


@app.command("create-video-campaigns")
def create_video_campaigns(
    app_name: str = typer.Option(..., help="App name to process (must match Video Assets rows)"),
    campaign_type: str = typer.Option(..., help="CampaignType to launch (e.g., AIVideoTesting)"),
    n: int = typer.Option(3, help="Max number of new videos to attach as ads"),
    budget_minor: Optional[int] = typer.Option(None, help="Override daily budget in minor units"),
):
    cfg = AppConfig.load_from_env()
    created = VideoCampaignAgent(cfg).create_video_campaigns(
        app_name=app_name, campaign_type=campaign_type, n=n, budget_minor_override=budget_minor
    )
    console.print(f"Created {created} video ads")
@app.command("prompt-experiment")
def prompt_experiment(
    app_name: str = typer.Option(..., help="App to ideate for"),
    prompts_dir: str = typer.Option(..., help="Directory containing *.txt prompt files"),
    n_per_prompt: int = typer.Option(3, help="Ideas to generate per prompt file"),
    generate_images: bool = typer.Option(
        True,
        "--generate-images/--no-generate-images",
        help="Whether to immediately generate images for new ideas",
        show_default=True,
    ),
):
    """Run ideation for each *.txt file in a directory, tagging rows with Prompt_Variant, optionally generate images."""
    cfg = AppConfig.load_from_env()
    agent = IdeationAgent(cfg)
    files = sorted(glob.glob(os.path.join(prompts_dir, "*.txt")))
    if not files:
        console.print("[red]No prompt files found[/red]")
        raise typer.Exit(code=1)
    total = 0
    for f in files:
        console.print(f"[blue]Ideating {n_per_prompt} with prompt: {os.path.basename(f)}[/blue]")
        total += agent.run(app_name=app_name, n=n_per_prompt, platform="Meta", prompt_file=f)
    console.print(f"[green]Total ideas appended: {total}[/green]")
    console.print("If you enabled image generation, images will be created next.")
    if generate_images:
        ImageGenerationAgent(cfg).run()


@app.command("user-prompt-experiment")
def user_prompt_experiment(
    app_name: str = typer.Option(..., help="App to ideate for"),
    user_prompts_dir: str = typer.Option(..., help="Directory containing user prompt *.txt templates"),
    n_per_prompt: int = typer.Option(3, help="Ideas to generate per user prompt file"),
    generate_images: bool = typer.Option(
        True,
        "--generate-images/--no-generate-images",
        help="Whether to immediately generate images for new ideas",
        show_default=True,
    ),
):
    """Run ideation across user prompt templates; tags rows with User_Prompt_Variant."""
    cfg = AppConfig.load_from_env()
    agent = IdeationAgent(cfg)
    files = sorted(glob.glob(os.path.join(user_prompts_dir, "*.txt")))
    if not files:
        console.print("[red]No user prompt files found[/red]")
        raise typer.Exit(code=1)
    total = 0
    for f in files:
        console.print(f"[blue]Ideating {n_per_prompt} with user prompt: {os.path.basename(f)}[/blue]")
        total += agent.run(app_name=app_name, n=n_per_prompt, platform="Meta", user_prompt_file=f)
    console.print(f"[green]Total ideas appended: {total}[/green]")
    console.print("If you enabled image generation, images will be created next.")
    if generate_images:
        ImageGenerationAgent(cfg).run()


@app.command("dual-prompt-experiment")
def dual_prompt_experiment(
    app_name: str = typer.Option(..., help="App to ideate for"),
    prompts_dir: str = typer.Option(..., help="Directory containing system prompt *.txt files"),
    user_prompts_dir: str = typer.Option(..., help="Directory containing user prompt *.txt templates"),
    n_per_combo: int = typer.Option(2, help="Ideas to generate per (system,user) prompt combination"),
    generate_images: bool = typer.Option(
        True,
        "--generate-images/--no-generate-images",
        help="Whether to immediately generate images for new ideas",
        show_default=True,
    ),
):
    """Run ideation across the Cartesian product of system and user prompt files; tags rows with Prompt_Variant and User_Prompt_Variant, optionally generate images."""
    cfg = AppConfig.load_from_env()
    agent = IdeationAgent(cfg)
    sys_files = sorted(glob.glob(os.path.join(prompts_dir, "*.txt")))
    user_files = sorted(glob.glob(os.path.join(user_prompts_dir, "*.txt")))
    if not sys_files:
        console.print("[red]No system prompt files found[/red]")
        raise typer.Exit(code=1)
    if not user_files:
        console.print("[red]No user prompt files found[/red]")
        raise typer.Exit(code=1)
    total = 0
    for sf in sys_files:
        for uf in user_files:
            console.print(f"[blue]Ideating {n_per_combo} with system: {os.path.basename(sf)} + user: {os.path.basename(uf)}[/blue]")
            total += agent.run(app_name=app_name, n=n_per_combo, platform="Meta", prompt_file=sf, user_prompt_file=uf)
    console.print(f"[green]Total ideas appended: {total}[/green]")
    console.print("If you enabled image generation, images will be created next.")
    if generate_images:
        ImageGenerationAgent(cfg).run()



@app.command()
def run(
    app_name: Optional[str] = typer.Option(None, help="App name for ideation"),
    n: Optional[int] = typer.Option(None, help="Number of ideas to generate"),
    platform: Optional[str] = typer.Option(None, help="Ad platform label (e.g., Meta)"),
    prompt_file: Optional[str] = typer.Option(None, help="Path to a system prompt file for ideation experiments"),
    user_prompt_file: Optional[str] = typer.Option(None, help="Path to a user prompt template file (supports {app_desc},{ios_url},{android_url},{platform},{n})"),
):
    """Run ideation then image generation."""
    cfg = AppConfig.load_from_env()
    IdeationAgent(cfg).run(app_name=app_name, n=n, platform=platform, prompt_file=prompt_file, user_prompt_file=user_prompt_file)
    ImageGenerationAgent(cfg).run()


@app.command("full-run")
def full_run(
    app_name: str = typer.Option(..., help="App name as in App List sheet (AppName)"),
    n: int = typer.Option(5, help="Number of ideas/images to create"),
    budget_minor: int = typer.Option(300, help="Daily budget in minor units (e.g., 500 = $5)"),
    prompt_file: Optional[str] = typer.Option(None, help="Path to a system prompt file for ideation experiments"),
    user_prompt_file: Optional[str] = typer.Option(None, help="Path to a user prompt template file (supports {app_desc},{ios_url},{android_url},{platform},{n})"),
):
    """Create one campaign with n images for a given app: ideate -> generate-images -> create-campaigns."""
    cfg = AppConfig.load_from_env()
    console.print(f"[blue]Ideating {n} ideas for {app_name}...[/blue]")
    IdeationAgent(cfg).run(app_name=app_name, n=n, platform="Meta", prompt_file=prompt_file, user_prompt_file=user_prompt_file)
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

