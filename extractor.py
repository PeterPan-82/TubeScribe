"""
Transcript extraction.
Primary: youtube-transcript-api (lighter, uses Innertube API, less bot-detectable).
Fallback: yt-dlp extract_info + direct subtitle download.

Cookies: set the YT_COOKIES environment variable to the full text of a
Netscape-format cookies.txt file exported from your browser while logged
into YouTube. Both extractors will use them.
"""
import re
import os
import logging
import tempfile
import yt_dlp

try:
    from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
    _HAS_YT_API = True
except ImportError:
    _HAS_YT_API = False

log = logging.getLogger(__name__)


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
        log.warning("YT_COOKIES not set — requests will be unauthenticated")
        return None
    # Handle Render possibly escaping newlines as literal \n
    cookies = cookies.replace("\\n", "\n")
    path = os.path.join(tmpdir, "cookies.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(cookies)
    log.info("Cookies file written (%d bytes)", len(cookies))
    return path


def _parse_vtt(content: str) -> str | None:
    """Extract plain text from a WebVTT/SRT subtitle file."""
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
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"&amp;", "&", line)
        line = re.sub(r"&lt;", "<", line)
        line = re.sub(r"&gt;", ">", line)
        line = line.strip()
        if line:
            texts.append(line)
    deduped = []
    for t in texts:
        if not deduped or t != deduped[-1]:
            deduped.append(t)
    text = " ".join(deduped)
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if text else None


# ---------------------------------------------------------------------------
# Extractor 1: youtube-transcript-api (primary)
# Uses the Innertube /get_transcript endpoint — lighter and less detectable
# ---------------------------------------------------------------------------

def _fetch_via_api(video_id: str, languages: list[str]) -> str | None:
    if not _HAS_YT_API:
        log.warning("youtube-transcript-api not available")
        return None
    with tempfile.TemporaryDirectory() as tmpdir:
        cf = _cookies_file(tmpdir)
        kwargs = {"cookies": cf} if cf else {}
        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id, **kwargs)
        except Exception as e:
            log.warning("YT-API list_transcripts failed: %s", e)
            return None

        transcript = None
        for lang in languages:
            try:
                transcript = transcript_list.find_manually_created_transcript([lang])
                log.info("Found manual transcript: %s", lang)
                break
            except Exception:
                pass
        if transcript is None:
            for lang in languages:
                try:
                    transcript = transcript_list.find_generated_transcript([lang])
                    log.info("Found auto transcript: %s", lang)
                    break
                except Exception:
                    pass
        if transcript is None:
            log.warning("No transcript found for languages %s", languages)
            return None

        try:
            entries = transcript.fetch()
            text = " ".join(e["text"] for e in entries)
            text = re.sub(r"\[.*?\]", "", text)
            text = re.sub(r"\s+", " ", text).strip()
            return text if text else None
        except Exception as e:
            log.warning("YT-API fetch failed: %s", e)
            return None


# ---------------------------------------------------------------------------
# Extractor 2: yt-dlp (fallback)
# Mirrors the working local script: writes subtitle .vtt to disk, reads it back.
# Uses download=True + skip_download=True so yt-dlp handles the subtitle fetch
# itself (no manual URL extraction or separate requests call).
# ---------------------------------------------------------------------------

def _fetch_via_ytdlp(video_id: str, languages: list[str]) -> str | None:
    import glob as _glob
    url = f"https://www.youtube.com/watch?v={video_id}"
    # Build subtitle language list: preferred langs + common English fallbacks
    sub_langs = languages + ["en", "en-US", "en-GB", "en.*"]
    # Deduplicate while preserving order
    seen = set()
    sub_langs = [l for l in sub_langs if not (l in seen or seen.add(l))]

    with tempfile.TemporaryDirectory() as tmpdir:
        base = os.path.join(tmpdir, video_id)
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,       # Don't download the video
            "writesubtitles": True,      # Write manual subtitles
            "writeautomaticsub": True,   # Write auto-generated subtitles
            "subtitleslangs": sub_langs,
            "subtitlesformat": "vtt",
            "outtmpl": base,
            "noplaylist": True,
            "ignoreerrors": True,
        }
        cf = _cookies_file(tmpdir)
        if cf:
            opts["cookiefile"] = cf

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
        except Exception as e:
            log.warning("yt-dlp extract_info failed: %s", e)
            return None

        # Find any .vtt file that was written
        vtt_files = _glob.glob(base + "*.vtt")
        if not vtt_files:
            log.warning("yt-dlp: no .vtt file written to disk")
            return None

        vtt_path = sorted(vtt_files)[0]
        log.info("yt-dlp: found subtitle file %s", os.path.basename(vtt_path))
        try:
            with open(vtt_path, "r", encoding="utf-8", errors="ignore") as f:
                return _parse_vtt(f.read())
        except Exception as e:
            log.warning("yt-dlp: failed to read vtt file: %s", e)
            return None


# ---------------------------------------------------------------------------
# Primary dispatch — api first, yt-dlp as fallback
# ---------------------------------------------------------------------------

def _fetch_transcript(video_id: str, languages: list[str]) -> str | None:
    log.info("Fetching transcript for %s", video_id)
    text = _fetch_via_api(video_id, languages)
    if text:
        log.info("Got transcript via youtube-transcript-api")
        return text
    log.info("Falling back to yt-dlp")
    text = _fetch_via_ytdlp(video_id, languages)
    if text:
        log.info("Got transcript via yt-dlp")
    else:
        log.warning("Both extractors failed for %s", video_id)
    return text


def _get_video_title(video_id: str) -> str:
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
        except Exception as e:
            log.warning("Channel/playlist enumeration failed: %s", e)
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

    for v in videos:
        if scope == "guaranteed":
            if target_n is not None and extracted >= target_n:
                break
            if scan_limit is not None and scanned >= scan_limit:
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
            target=target_n,
            video_title=v["title"],
            success=success
        )

    return results
