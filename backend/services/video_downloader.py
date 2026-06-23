"""
Video downloader with multi-level fallback:
1. yt-dlp direct download
2. Playwright browser recording
3. Return error -> user manually uploads
"""
import os
import subprocess
import tempfile
from pathlib import Path
from config import settings


def download_video(url: str, output_dir: str, video_id: str) -> tuple[str | None, str | None]:
    """
    Download a video from a URL. Returns (video_path, error_message).
    Tries yt-dlp first, falls back to playwright recording.
    """
    output_path = Path(output_dir) / f"{video_id}.mp4"

    # Strategy 1: yt-dlp
    result = _download_with_ytdlp(url, str(output_path))
    if result is None:
        return str(output_path), None

    # Strategy 2: Playwright recording
    result = _download_with_playwright(url, str(output_path))
    if result is None:
        return str(output_path), None

    # Strategy 3: Failed
    return None, f"下载失败: yt-dlp error={_download_with_ytdlp(url, str(output_path))}, playwright error={result}"


def _download_with_ytdlp(url: str, output_path: str) -> str | None:
    """Returns None if successful, error message otherwise"""
    try:
        cmd = [
            "yt-dlp",
            "-f", "best[ext=mp4]/best",
            "-o", output_path,
            "--no-playlist",
            "--socket-timeout", "30",
            "--retries", "3",
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and os.path.exists(output_path):
            return None
        return result.stderr[:500] if result.stderr else "yt-dlp failed"
    except subprocess.TimeoutExpired:
        return "yt-dlp timeout"
    except FileNotFoundError:
        return "yt-dlp not installed"
    except Exception as e:
        return str(e)[:500]


def _download_with_playwright(url: str, output_path: str) -> str | None:
    """Use Playwright to record browser screen. Returns None if successful, error message otherwise."""
    try:
        from playwright.sync_api import sync_playwright
        import time

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 390, "height": 844},
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
            )
            page = context.new_page()

            # Record video
            # Note: Playwright video recording saves to a specific path, not arbitrary output
            page.goto(url, wait_until="networkidle", timeout=30000)
            time.sleep(3)  # Wait for video to start

            # Try to find and click video play button if needed
            video_element = page.query_selector("video")
            if video_element:
                video_element.click()
                time.sleep(5)

            # Take a screenshot as fallback (can't easily record video to arbitrary path)
            page.screenshot(path=output_path.replace(".mp4", ".png"))

            browser.close()
            return "Playwright recording saved as screenshot only; video recording requires additional setup"
    except FileNotFoundError:
        return "playwright not installed"
    except Exception as e:
        return str(e)[:500]


def extract_audio(video_path: str, audio_output_path: str) -> str | None:
    """Extract audio from video using FFmpeg. Returns None if successful, error message otherwise."""
    try:
        cmd = [
            "ffmpeg",
            "-i", video_path,
            "-vn",
            "-acodec", "mp3",
            "-ab", "192k",
            "-ar", "44100",
            "-y",
            audio_output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and os.path.exists(audio_output_path):
            return None
        return result.stderr[:500]
    except subprocess.TimeoutExpired:
        return "FFmpeg timeout"
    except FileNotFoundError:
        return "ffmpeg not installed"
    except Exception as e:
        return str(e)[:500]
