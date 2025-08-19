from __future__ import annotations

import base64
import io
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import cv2  # type: ignore
import numpy as np  # type: ignore
from openai import OpenAI
from pydantic import BaseModel, Field
from rich.console import Console
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import AppConfig
from .google_drive import DriveClient
from .google_sheets import SheetsClient


console = Console()


@dataclass
class VideoFrames:
    frames_b64: List[str]
    fps: float
    duration_sec: float


class CreativeAnalysisAgent:
    def __init__(self, cfg: AppConfig):
        if not cfg.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required")
        self._cfg = cfg
        self._client = OpenAI(api_key=cfg.openai_api_key)
        self._drive = DriveClient(cfg)
        self._sheets = SheetsClient(cfg)

    # ---- Structured output model ----
    class CreativeAnalysis(BaseModel):
        target_audience: str = Field(..., description="Who the creative targets")
        hook_first_3_4_seconds: str = Field(..., description="Hook in the first 3-4 seconds (or first glance for static)")
        app_showcase: str = Field(..., description="How the app is presented: Demo, Real usage, Scenario, Motion graphics, etc.")
        product_feature_benefits_outlined: str = Field(..., description="Key product features / benefits called out")
        video_storyline: str = Field(..., description="Short storyline; include how it relates with the product")
        video_or_static: str = Field(..., description="One of 'Video' or 'Static'")
        ugc_ai_or_ugc_real: str = Field(..., description="UGC AI or UGC Real")
        cta: str = Field(..., description="Call to action text shown or implied")
        analyst_notes: str = Field(default="", description="Other notable factors: editing, pacing, brand cues, compliance, visuals, music, captions")

    def _analysis_to_fields(self, ca: "CreativeAnalysisAgent.CreativeAnalysis") -> Dict[str, str]:
        return {
            "Target Audience": ca.target_audience,
            "Hook first 3-4 seconds": ca.hook_first_3_4_seconds,
            "App Showcase": ca.app_showcase,
            "Product Feature / Benefits Outlined": ca.product_feature_benefits_outlined,
            "Video storyline": ca.video_storyline,
            "Video / Static": ca.video_or_static,
            "UGC AI / UGC Real": ca.ugc_ai_or_ugc_real,
            "CTA": ca.cta,
            "Analyst_Notes": ca.analyst_notes,
        }

    # ---- Google Drive scanning ----
    def _list_drive_assets(self) -> List[Dict]:
        folder_id = self._cfg.competitor_creatives_folder_id
        if not folder_id:
            raise RuntimeError("COMPETITOR_CREATIVES_FOLDER_ID must be configured")
        videos = self._drive.list_videos_in_folder(folder_id)
        images = self._drive.list_images_in_folder(folder_id)
        assets: List[Dict] = []
        for f in videos:
            assets.append({
                "File_ID": f.get("id"),
                "File_Name": f.get("name"),
                "File_URL": f.get("webViewLink", ""),
                "MimeType": f.get("mimeType", ""),
                "ModifiedTime": f.get("modifiedTime", ""),
                "IsVideo": "Yes",
            })
        for f in images:
            assets.append({
                "File_ID": f.get("id"),
                "File_Name": f.get("name"),
                "File_URL": f.get("webViewLink", ""),
                "MimeType": f.get("mimeType", ""),
                "ModifiedTime": f.get("modifiedTime", ""),
                "IsVideo": "No",
            })
        return assets

    # ---- Media helpers ----
    def _decode_video(self, video_bytes: bytes) -> Tuple[cv2.VideoCapture, str]:
        tmp_path = os.path.join(os.getcwd(), f"_tmp_video_{os.getpid()}.mp4")
        with open(tmp_path, "wb") as f:
            f.write(video_bytes)
        return cv2.VideoCapture(tmp_path), tmp_path

    def _extract_audio_bytes(self, video_bytes: bytes) -> bytes:
        # Use OpenCV to dump audio is non-trivial; instead, rely on ffmpeg via imageio-ffmpeg
        import imageio_ffmpeg
        import subprocess
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as vf:
            vf.write(video_bytes)
            vf_path = vf.name
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as af:
            af_path = af.name
        cmd = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-i",
            vf_path,
            "-vn",
            "-acodec",
            "libmp3lame",
            "-ar",
            "44100",
            "-ac",
            "2",
            af_path,
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with open(af_path, "rb") as f:
            data = f.read()
        try:
            os.remove(vf_path)
            os.remove(af_path)
        except Exception:
            pass
        return data

    def _sample_frames(self, cap: cv2.VideoCapture, every_n: int = 25, max_frames: int = 40) -> VideoFrames:
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = frame_count / fps if fps > 0 else 0
        frames_b64: List[str] = []
        success = True
        i = 0
        while success:
            success, frame = cap.read()
            if not success:
                break
            if i % every_n == 0:
                _, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                frames_b64.append(base64.b64encode(buffer).decode("utf-8"))
                if len(frames_b64) >= max_frames:
                    break
            i += 1
        return VideoFrames(frames_b64=frames_b64, fps=fps, duration_sec=duration)

    # ---- OpenAI helpers ----
    @retry(wait=wait_exponential(multiplier=1, min=1, max=20), stop=stop_after_attempt(4))
    def _transcribe(self, audio_bytes: bytes) -> str:
        file_obj = io.BytesIO(audio_bytes)
        file_obj.name = "audio.mp3"
        t = self._client.audio.transcriptions.create(
            model="gpt-4o-transcribe",  # OpenAI Transcriptions endpoint model
            file=(file_obj, "audio.mp3"),
            response_format="text",
        )
        if hasattr(t, "text"):
            return t.text  # type: ignore[attr-defined]
        # Fallback for SDK variants returning raw string
        return str(t)

    @retry(wait=wait_exponential(multiplier=1, min=1, max=20), stop=stop_after_attempt(4))
    def _analyze_static(self, image_b64: str) -> Dict[str, str]:
        content: List[Dict] = [
            {"type": "input_text", "text": self._analysis_prompt(is_video=False) },
            {"type": "input_image", "image_url": f"data:image/jpeg;base64,{image_b64}"},
        ]
        # Try structured output first
        try:
            resp = self._client.responses.parse(
                model="gpt-4o",
                input=[{"role": "user", "content": content}],
                text_format=CreativeAnalysisAgent.CreativeAnalysis,
            )
            parsed: CreativeAnalysisAgent.CreativeAnalysis = resp.output_parsed  # type: ignore[assignment]
            return self._analysis_to_fields(parsed)
        except Exception:
            # Fallback: free-form response
            resp = self._client.responses.create(model="gpt-4.1-mini", input=[{"role": "user", "content": content}])
            return self._parse_analysis_text(resp.output_text or "")

    @retry(wait=wait_exponential(multiplier=1, min=1, max=20), stop=stop_after_attempt(4))
    def _analyze_video(self, frames_b64: List[str], transcript: str) -> Dict[str, str]:
        content: List[Dict] = [
            {"type": "input_text", "text": self._analysis_prompt(is_video=True, transcript=transcript) },
        ]
        for b in frames_b64:
            content.append({"type": "input_image", "image_url": f"data:image/jpeg;base64,{b}"})
        # Try structured output first
        try:
            resp = self._client.responses.parse(
                model="gpt-4o",
                input=[{"role": "user", "content": content}],
                text_format=CreativeAnalysisAgent.CreativeAnalysis,
            )
            parsed: CreativeAnalysisAgent.CreativeAnalysis = resp.output_parsed  # type: ignore[assignment]
            return self._analysis_to_fields(parsed)
        except Exception:
            # Fallback: free-form response
            resp = self._client.responses.create(model="gpt-4.1-mini", input=[{"role": "user", "content": content}])
            return self._parse_analysis_text(resp.output_text or "")

    def _analysis_prompt(self, is_video: bool, transcript: Optional[str] = None) -> str:
        base = (
            "You are a performance creative analyst. Produce structured output to exactly match this schema: \n"
            "- target_audience: string\n"
            "- hook_first_3_4_seconds: string\n"
            "- app_showcase: string (one of: Demo, Real usage, Scenario, Motion graphics, or brief description)\n"
            "- product_feature_benefits_outlined: string\n"
            "- video_storyline: string (short story arc; include: How is it related with product?)\n"
            "- video_or_static: string (exactly 'Video' or 'Static')\n"
            "- ugc_ai_or_ugc_real: string (exactly 'UGC AI' or 'UGC Real' if applicable, else short descriptor)\n"
            "- cta: string\n"
            "- analyst_notes: string (other notable factors: editing, pacing, brand cues, compliance, visuals, music, captions)\n"
            "Ensure fields are concise and useful for marketing analysis.\n"
        )
        if is_video and transcript:
            base += "\nTranscript (for context):\n" + transcript[:8000]
        return base

    def _parse_analysis_text(self, text: str) -> Dict[str, str]:
        out = {
            "Target Audience": "",
            "Hook first 3-4 seconds": "",
            "App Showcase": "",
            "Product Feature / Benefits Outlined": "",
            "Video / Static": "",
            "UGC AI / UGC Real": "",
            "CTA": "",
        }
        for line in (text or "").splitlines():
            parts = line.split(":", 1)
            if len(parts) != 2:
                continue
            key = parts[0].strip()
            val = parts[1].strip()
            if key in out:
                out[key] = val
        return out

    # ---- Main entrypoint ----
    def run(self, app_name: Optional[str] = None, limit: Optional[int] = None) -> int:
        assets = self._list_drive_assets()
        if not assets:
            console.print("[yellow]No assets found in Drive folder[/yellow]")
            return 0
        new_rows: List[Dict[str, str]] = []
        # Append any files that do not yet exist in analysis sheet
        for a in assets:
            if self._sheets.find_analysis_by_file_id(a["File_ID"]):
                continue
            row = {
                "Status": "New",
                "App_Name": app_name or "",
                **a,
                "Model": "gpt-4o",
            }
            new_rows.append(row)
        if new_rows:
            self._sheets.append_analysis_rows(new_rows)

        # Process rows that are not analyzed
        pending = self._sheets.list_unanalyzed_files()
        # Filter pending by app if provided
        if app_name:
            pending = [r for r in pending if str(r.get("App_Name", "")).strip().lower() in {"", app_name.strip().lower()}]
        if limit:
            pending = pending[:limit]
        if not pending:
            console.print("[yellow]No unanalyzed rows to process[/yellow]")
            return 0

        updates: Dict[int, Dict[str, str]] = {}
        completed = 0
        for row in pending:
            row_index = row.get("_row_index")
            if not row_index:
                continue
            updates[row_index] = {"Status": "Analyzing"}
        if updates:
            self._sheets.update_analysis_rows(updates)
        updates.clear()

        for row in pending:
            row_index = row["_row_index"]
            try:
                file_id = str(row.get("File_ID", ""))
                mime = str(row.get("MimeType", ""))
                is_video = "video" in mime or str(row.get("IsVideo", "")).lower() == "yes"
                file_bytes = self._drive.download_file_bytes(file_id)
                transcript = ""
                analysis: Dict[str, str] = {}
                if is_video:
                    cap, tmp_path = self._decode_video(file_bytes)
                    try:
                        frames = self._sample_frames(cap, every_n=25, max_frames=40)
                    finally:
                        cap.release()
                        try:
                            os.remove(tmp_path)
                        except Exception:
                            pass
                    try:
                        audio_bytes = self._extract_audio_bytes(file_bytes)
                        if not audio_bytes:
                            transcript = "No audio track detected."
                        else:
                            try:
                                transcript = self._transcribe(audio_bytes).strip()
                                if not transcript:
                                    transcript = "No speech detected."
                            except Exception:
                                transcript = "Transcription failed."
                    except Exception:
                        transcript = "No audio track detected or extraction failed."
                    analysis = self._analyze_video(frames.frames_b64, transcript)
                else:
                    # static image
                    img = cv2.imdecode(np.frombuffer(file_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
                    _, buffer = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                    b64 = base64.b64encode(buffer).decode("utf-8")
                    analysis = self._analyze_static(b64)

                updates[row_index] = {
                    "Status": "Analyzed",
                    "Target Audience": analysis.get("Target Audience", ""),
                    "Hook first 3-4 seconds": analysis.get("Hook first 3-4 seconds", ""),
                    "App Showcase": analysis.get("App Showcase", ""),
                    "Product Feature / Benefits Outlined": analysis.get("Product Feature / Benefits Outlined", ""),
                    "Video storyline": analysis.get("Video storyline", ""),
                    "Video / Static": analysis.get("Video / Static", "Video" if is_video else "Static"),
                    "UGC AI / UGC Real": analysis.get("UGC AI / UGC Real", ""),
                    "CTA": analysis.get("CTA", ""),
                    "Transcript": transcript,
                    "length": f"{int(round(frames.duration_sec))} sec" if is_video else "Static",
                }
                completed += 1
                console.print(f"[green]Analyzed row {row_index} ({'video' if is_video else 'image'})[/green]")
            except Exception as e:  # noqa: BLE001
                updates[row_index] = {
                    "Status": "Failed",
                    "Analyst_Notes": str(e),
                }
                console.print(f"[red]Failed analysis for row {row_index}: {e}[/red]")

        if updates:
            self._sheets.update_analysis_rows(updates)
        return completed


