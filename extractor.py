"""
Transcript extraction using yt-dlp (primary) and youtube-transcript-api (fallback).
yt-dlp's subtitle download is more robust against cloud IP blocking.
Cookies can be supplied via the YT_COOKIES environment variable to further
reduce blocking — set it to the full text of a Netscape-format cookies.txt file
exported from your browser while logged into YouTube.
"""
import re
import os
import glob
import tempfile
import yt_dlp

try:
    from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
    _HAS_YT_API = True
except ImportError:
    _HAS_YT_API = False


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


def _cookies_file(tmpdir: str) -> str | None:
    """Write YT_COOKIES env var to a temp file and return its path, or None."""
    cookies = os.environ.get("YT_COOKIES", "").strip()
    if not cookies:
        return None
    path = os.path.join(tmpdir, "cookies.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(cookies)
    return path


def _parse_vtt(content: str) -> str | None:
    """Extract plain text from a WebVTT subtitle file, deduplicating repeated lines."""
    lines = content.split("\n")
    texts = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("WEBVTT") or line.startswith("NOTE") or "-->" in line:
            continue
        if re.match(r"^\d+$", line):
            continue
        line = re.sub(r"<[^>]+>", "", line)   # strip HTML tags
        line = re.sub(r"&amp;", "&", line)
        line = re.sub(r"&lt;", "<", line)
        line = re.sub(r"&gt;", ">", line)
        line = line.strip()
        if line:
            texts.append(line)
    # Deduplicate consecutive identical lines (VTT often repeats rolling captions)
    deduped = []
    for t in texts:
        if not deduped or t != deduped[-1]:
            deduped.append(t)
    text = " ".join(deduped)
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if text else None


def _fetch_via_ytdlp(video_id: str, languages: list[str]) -> str | None:
    """
    Extract subtitle URLs from video info via yt-dlp, then download directly.
    This avoids the format-selection errors that occur with writesubtitles+skip_download.
    """
    import requests as req
    url = f"https://www.youtube.com/watch?v={video_id}"
    with tempfile.TemporaryDirectory() as tmpdir:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
        }
        cf = _cookies_file(tmpdir)
        if cf:
            opts["cookiefile"] = cf

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception:
            return None

        if not info:
            return None

        # Search manual subtitles first, then auto-captions
        all_subs = info.get("subtitles", {})
        auto_subs = info.get("automatic_captions", {})

        sub_url = None
        for lang in languages + ["en"]:
            for source in (all_subs, auto_subs):
                if lang not in source:
                    continue
                formats = source[lang]
                # Prefer VTT, then any available format
                for preferred_ext in ("vtt", "ttml", "srv3", "srv2", "srv1", "json3"):
                    for fmt in formats:
                        if fmt.get("ext") == preferred_ext and fmt.get("url"):
                            sub_url = fmt["url"]
                            break
                    if sub_url:
                        break
                if not sub_url:
                    for fmt in formats:
                        if fmt.get("url"):
                            sub_url = fmt["url"]
                            break
            if sub_url:
                break

        if not sub_url:
            return None

        # Download the subtitle file directly
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            r = req.get(sub_url, headers=headers, timeout=15)
            r.raise_for_status()
            return _parse_vtt(r.text)
        except Exception:
            return None


def _fetch_via_api(video_id: str, languages: list[str]) -> str | None:
    """Fallback: use youtube-transcript-api."""
    if not _HAS_YT_API:
        return None
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        transcript = None
        for lang in languages:
            try:
                transcript = transcript_list.find_manually_created_transcript([lang])
                break
            except Exception:
                pass
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
        text = re.sub(r"\[.*?\]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text if text else None
    except Exception:
        return None


def _fetch_transcript(video_id: str, languages: list[str]) -> str | None:
    """Try yt-dlp first, fall back to youtube-transcript-api."""
    text = _fetch_via_ytdlp(video_id, languages)
    if text:
        return text
    return _fetch_via_api(video_id, languages)


def _get_video_title(video_id: str) -> str:
    """Best-effort title fetch via yt-dlp."""
    with tempfile.TemporaryDirectory() as tmpdir:
        opts = {"quiet": True, "skip_download": True, "no_warnings": True}
        cf = _cookies_file(tmpdir)
        if cf:
            opts["cookiefile"] = cf
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(
                    f"https://www.youtube.com/watch?v={video_id}", download=False
                )
                return info.get("title", video_id)
        except Exception:
            return video_id


def _iter_channel_or_playlist_videos(url: str) -> list[dict]:
    """Return list of {id, title, url} dicts for all videos in channel/playlist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        opts = {
            "quiet": True,
            "skip_download": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "playlistend": 5000,
        }
        cf = _cookies_file(tmpdir)
        if cf:
            opts["cookiefile"] = cf
        videos = []
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                for e in info.get("entries", []):
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
    videos = _iter_channel_or_playlist_videos(url)
    if not videos:
        return []

    results = []
    scanned = 0
    extracted = 0
    failed = 0

    if scope == "latest":
        videos = videos[:target_n]

    effective_target = target_n
    effective_scan_limit = scan_limit

    for v in videos:
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
