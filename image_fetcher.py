"""
Fetch relevant images from Wikimedia Commons.
Returns None if nothing sufficiently relevant is found — no filler images.
"""
import requests

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
THUMB_API = "https://commons.wikimedia.org/w/api.php"

# Minimum title-match confidence: the search result title must share at least
# one keyword with our query for us to accept the image.
MIN_KEYWORD_OVERLAP = 1


def _search_commons(query: str, limit: int = 5) -> list[dict]:
    """Search Wikimedia Commons for images matching query."""
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srnamespace": "6",  # File namespace
        "srlimit": limit,
        "format": "json",
        "srprop": "title|snippet"
    }
    try:
        r = requests.get(COMMONS_API, params=params, timeout=8)
        r.raise_for_status()
        data = r.json()
        return data.get("query", {}).get("search", [])
    except Exception:
        return []


def _get_image_url(file_title: str) -> dict | None:
    """Resolve a Commons file title to a thumbnail URL and caption."""
    params = {
        "action": "query",
        "titles": file_title,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": 800,
        "format": "json"
    }
    try:
        r = requests.get(THUMB_API, params=params, timeout=8)
        r.raise_for_status()
        data = r.json()
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            info_list = page.get("imageinfo", [])
            if not info_list:
                continue
            info = info_list[0]
            url = info.get("thumburl") or info.get("url")
            if not url:
                continue
            # Only accept actual image files
            if not any(url.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg")):
                continue
            meta = info.get("extmetadata", {})
            caption = (
                meta.get("ImageDescription", {}).get("value", "")
                or meta.get("ObjectName", {}).get("value", "")
                or file_title.replace("File:", "").rsplit(".", 1)[0]
            )
            # Strip HTML from caption
            import re
            caption = re.sub(r"<[^>]+>", "", caption).strip()
            return {"url": url, "caption": caption[:120]}
    except Exception:
        pass
    return None


def _keywords_overlap(title: str, keywords: list[str]) -> int:
    title_lower = title.lower()
    return sum(1 for kw in keywords if kw.lower() in title_lower)


def fetch_image_for_section(keywords: list[str], section_heading: str) -> dict | None:
    """
    Given a list of keywords and a section heading, try to find a relevant
    Wikimedia Commons image. Returns {url, caption} or None.
    """
    if not keywords:
        return None

    query = " ".join(keywords[:3])
    results = _search_commons(query, limit=8)

    for result in results:
        title = result.get("title", "")
        overlap = _keywords_overlap(title, keywords)
        if overlap >= MIN_KEYWORD_OVERLAP:
            img = _get_image_url(title)
            if img:
                return img

    # Fallback: try with just the section heading words
    heading_words = [w for w in section_heading.lower().split() if len(w) > 3]
    if heading_words and heading_words != keywords[:len(heading_words)]:
        results2 = _search_commons(" ".join(heading_words[:2]), limit=5)
        for result in results2:
            title = result.get("title", "")
            if _keywords_overlap(title, heading_words) >= MIN_KEYWORD_OVERLAP:
                img = _get_image_url(title)
                if img:
                    return img

    return None  # Nothing relevant found — section runs without image
