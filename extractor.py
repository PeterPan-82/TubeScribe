"""
Transcript extraction using youtube-transcript-api (per video)
and yt-dlp (channel / playlist enumeration).
"""
import re
from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
import yt_dlp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _video_id_from_url(url: str) -> str | None:
    patterns = [
        r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})",
        r"(?:embed/)([A-Za-z0-9_-]{11})",
        r"(?:shorts/)([A-Za-z0-9_-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def _fetch_transcript(video_id: str, languages: list[str]) -> str | None:
    """Return cleaned transcript text or None."""
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        # Prefer manual over auto-generated, in language priority order
        transcript = None
        # Try manual first
        for lang in languages:
            try:
                transcript = transcript_list.find_manually_created_transcript([lang])
                break
            except Exception:
                pass
        # Fall back to auto-generated
        if transcript is None:
            for lang in languages:
                try:
                    transcript = transcript_list.find_generated_transcript([lang])
                    break
                except Exception:
                    pass
        if transcript is None:
            return None
        entries = transcript.fetch()
        text = " ".join(e["text"] for e in entries)
        # Clean up whitespace and common artefacts
        text = re.sub(r"\[.*?\]", "", text)          # remove [Music], [Applause] etc.
        text = re.sub(r"\s+", " ", text).strip()
        return text if text else None
    except (NoTranscriptFound, TranscriptsDisabled):
        return None
    except Exception:
        return None


def _get_video_title(video_id: str) -> str:
    """Best-effort title fetch via yt-dlp (quiet)."""
    try:
        opts = {"quiet": True, "skip_download": True, "no_warnings": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            return info.get("title", video_id)
    except Exception:
        return video_id


def _iter_channel_or_playlist_videos(url: str) -> list[dict]:
    """Return list of {id, title, url} dicts for all videos in channel/playlist."""
    opts = {
        "quiet": True,
        "skip_download": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "playlistend": 5000,  # hard ceiling
    }
    videos = []
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            entries = info.get("entries", [])
            for e in entries:
                vid_id = e.get("id") or e.get("url", "")
                if len(vid_id) == 11:
                    videos.append({
                        "id": vid_id,
                        "title": e.get("title", vid_id),
                        "url": f"https://www.youtube.com/watch?v={vid_id}"
                    })
    except Exception:
        pass
    return videos


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_single(url: str, languages: list[str]) -> dict | None:
    """Extract transcript from a single video URL. Returns {title, text, video_url} or None."""
    video_id = _video_id_from_url(url)
    if not video_id:
        return None
    text = _fetch_transcript(video_id, languages)
    if not text:
        return None
    title = _get_video_title(video_id)
    return {"title": title, "text": text, "video_url": url}


def extract_multi(
    url: str,
    source_type: str,
    scope: str,
    target_n: int | None,
    scan_limit: int | None,
    languages: list[str],
    progress_cb
) -> list[dict]:
    """
    Extract transcripts from a channel or playlist.

    scope:
      "all"        – extract everything available
      "latest"     – scan first target_n videos, take whatever yields transcripts
      "guaranteed" – keep scanning until target_n successes or scan_limit reached
    """
    videos = _iter_channel_or_playlist_videos(url)
    if not videos:
        return []

    results = []
    scanned = 0
    extracted = 0
    failed = 0

    # For "all" we process every video
    # For "latest" we only look at the first target_n
    if scope == "latest":
        videos = videos[:target_n]

    effective_target = target_n  # None means "no limit"
    effective_scan_limit = scan_limit  # None means scan all

    for v in videos:
        # Check stopping conditions
        if scope == "guaranteed":
            if effective_target is not None and extracted >= effective_target:
                break
            if effective_scan_limit is not None and scanned >= effective_scan_limit:
                break

        text = _fetch_transcript(v["id"], languages)
        scanned += 1
        success = text is not None

        if success:
            extracted += 1
            results.append({"title": v["title"], "text": text, "video_url": v["url"]})
        else:
            failed += 1

        progress_cb(
            scanned=scanned,
            extracted=extracted,
            failed=failed,
            target=effective_target,
            video_title=v["title"],
            success=success
        )

    return results
