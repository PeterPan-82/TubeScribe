"""
Transcript extraction.
Primary: youtube-transcript-api (lighter, uses Innertube API, less bot-detectable).
Fallback: yt-dlp — writes subtitle .vtt to disk, reads it back.

Cookies: set the YT_COOKIES environment variable to the full text of a
Netscape-format cookies.txt file exported from your browser while logged
into YouTube. Both extractors will use them.
"""
import re
import os
import tempfile
import glob as _glob
import yt_dlp

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    _HAS_YT_API = True
except ImportError:
    _HAS_YT_API = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _p(msg):
    """Print to stdout so it always appears in Render logs."""
    print(f"[extractor] {msg}", flush=True)


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
        _p("WARNING: YT_COOKIES not set — requests will be unauthenticated")
        return None
    # Render sometimes stores literal \n instead of real newlines
    cookies = cookies.replace("\\n", "\n")
    path = os.path.join(tmpdir, "cookies.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(cookies)
    _p(f"Cookies file written ({len(cookies)} bytes, {cookies.count(chr(10))} lines)")
    return path


def _parse_vtt(content: str) -> str | None:
    """Extract plain text from a WebVTT subtitle file."""
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
        # Strip VTT timing tags like <00:00:01.234>
        line = re.sub(r"<\d{2}:\d{2}:\d{2}\.\d{3}>", " ", line)
        line = re.sub(r"</?c>", " ", line)
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"&amp;", "&", line)
        line = re.sub(r"&lt;", "<", line)
        line = re.sub(r"&gt;", ">", line)
        line = re.sub(r"&nbsp;", " ", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            texts.append(line)
    # Deduplicate consecutive identical lines
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
# ---------------------------------------------------------------------------

def _fetch_via_api(video_id: str, languages: list[str]) -> str | None:
    if not _HAS_YT_API:
        _p("youtube-transcript-api not installed, skipping")
        return None
    _p(f"Trying youtube-transcript-api for {video_id} ...")
    with tempfile.TemporaryDirectory() as tmpdir:
        cf = _cookies_file(tmpdir)
        kwargs = {"cookies": cf} if cf else {}
        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id, **kwargs)
        except Exception as e:
            _p(f"YT-API list_transcripts FAILED: {e}")
            return None

        transcript = None
        # Try manual transcripts first, then auto-generated
        for lang in languages:
            try:
                transcript = transcript_list.find_manually_created_transcript([lang])
                _p(f"Found manual transcript: {lang}")
                break
            except Exception:
                pass
        if transcript is None:
            for lang in languages:
                try:
                    transcript = transcript_list.find_generated_transcript([lang])
                    _p(f"Found auto-generated transcript: {lang}")
                    break
                except Exception:
                    pass
        if transcript is None:
            _p(f"YT-API: no transcript for languages {languages}")
            return None

        try:
            entries = transcript.fetch()
            text = " ".join(e["text"] for e in entries)
            text = re.sub(r"\[.*?\]", "", text)
            text = re.sub(r"\s+", " ", text).strip()
            _p(f"YT-API SUCCESS: {len(text)} chars")
            return text if text else None
        except Exception as e:
            _p(f"YT-API fetch FAILED: {e}")
            return None


# ---------------------------------------------------------------------------
# Extractor 2: yt-dlp (fallback)
# Writes subtitle .vtt to a temp dir, reads it back — same as the working
# local Colab script.
# ---------------------------------------------------------------------------

def _fetch_via_ytdlp(video_id: str, languages: list[str]) -> str | None:
    _p(f"Trying yt-dlp for {video_id} ...")
    url = f"https://www.youtube.com/watch?v={video_id}"
    # Build full language list with English fallbacks
    sub_langs = list(dict.fromkeys(languages + ["en", "en-US", "en-GB", "en.*"]))

    with tempfile.TemporaryDirectory() as tmpdir:
        base = os.path.join(tmpdir, video_id)
        opts = {
            "quiet": False,          # Let yt-dlp print to stdout so we see errors
            "no_warnings": False,
            "skip_download": True,   # Don't download the video itself
            "writesubtitles": True,
            "writeautomaticsub": True,
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
            _p(f"yt-dlp extract_info FAILED: {e}")
            return None

        vtt_files = _glob.glob(base + "*.vtt")
        _p(f"yt-dlp: .vtt files found after extraction: {[os.path.basename(f) for f in vtt_files]}")
        if not vtt_files:
            _p("yt-dlp: no subtitle file written — bot block or no captions available")
            return None

        vtt_path = sorted(vtt_files)[0]
        try:
            with open(vtt_path, "r", encoding="utf-8", errors="ignore") as f:
                result = _parse_vtt(f.read())
            if result:
                _p(f"yt-dlp SUCCESS: {len(result)} chars from {os.path.basename(vtt_path)}")
            else:
                _p("yt-dlp: vtt parsed but empty")
            return result
        except Exception as e:
            _p(f"yt-dlp: failed to read vtt file: {e}")
            return None


# ---------------------------------------------------------------------------
# Primary dispatch
# ---------------------------------------------------------------------------

def _fetch_transcript(video_id: str, languages: list[str]) -> str | None:
    _p(f"=== Fetching transcript for video_id={video_id}, languages={languages} ===")
    text = _fetch_via_api(video_id, languages)
    if text:
        return text
    _p("youtube-transcript-api failed, falling back to yt-dlp")
    text = _fetch_via_ytdlp(video_id, languages)
    if not text:
        _p(f"=== BOTH extractors failed for {video_id} ===")
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
                return info.get("title", video_id) if info else video_id
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
                for e in (info or {}).get("entries", []):
                    if not e:
                        continue
                    vid_id = e.get("id") or ""
                    if len(vid_id) == 11:
                        videos.append({
                            "id": vid_id,
                            "title": e.get("title", vid_id),
                            "url": f"https://www.youtube.com/watch?v={vid_id}"
                        })
        except Exception as e:
            _p(f"Channel/playlist enumeration failed: {e}")
    return videos


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_single(url: str, languages: list[str]) -> dict | None:
    video_id = _video_id_from_url(url)
    if not video_id:
        _p(f"Could not extract video_id from URL: {url}")
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
        _p("No videos found in channel/playlist")
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
