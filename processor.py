"""
Rule-based transcript processing.
Transforms raw transcript dicts into structured content ready for formatting.
"""
import re
from image_fetcher import fetch_image_for_section

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FILLER_WORDS = {
    "um", "uh", "ah", "er", "hmm", "like", "you know", "i mean",
    "sort of", "kind of", "basically", "literally", "actually",
    "so", "right", "okay", "ok", "well", "anyway", "alright"
}

def _clean_text(text: str) -> str:
    """Remove filler words and normalise whitespace."""
    for fw in sorted(FILLER_WORDS, key=len, reverse=True):
        text = re.sub(r'\b' + re.escape(fw) + r'\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _split_sentences(text: str) -> list[str]:
    """Naive sentence splitter that works across EN/DE/ES/FA."""
    parts = re.split(r'(?<=[.!?])\s+', text)
    return [p.strip() for p in parts if p.strip()]


def _score_sentence(sentence: str, word_freq: dict, index: int, total: int) -> float:
    """Score a sentence for summary extraction."""
    words = re.findall(r'\w+', sentence.lower())
    if not words:
        return 0.0
    # Word frequency score
    freq_score = sum(word_freq.get(w, 0) for w in words) / len(words)
    # Position bonus: first and last 10% of sentences score higher
    pos_ratio = index / max(total - 1, 1)
    pos_bonus = 1.3 if pos_ratio < 0.1 or pos_ratio > 0.9 else 1.0
    # Length penalty: very short sentences (< 6 words) are usually not informative
    length_penalty = 0.5 if len(words) < 6 else 1.0
    return freq_score * pos_bonus * length_penalty


def _summarise(text: str, ratio: float = 0.25) -> str:
    """Extract top sentences by score, preserving original order."""
    sentences = _split_sentences(text)
    if len(sentences) <= 4:
        return text
    # Build word frequency map (ignore stop words)
    stop = {"the","a","an","is","are","was","were","be","been","being",
            "have","has","had","do","does","did","will","would","could",
            "should","may","might","shall","can","and","or","but","in",
            "on","at","to","for","of","with","by","from","as","into","it"}
    words_all = [w for w in re.findall(r'\w+', text.lower()) if w not in stop]
    freq: dict = {}
    for w in words_all:
        freq[w] = freq.get(w, 0) + 1
    total = len(sentences)
    scored = [(i, s, _score_sentence(s, freq, i, total)) for i, s in enumerate(sentences)]
    keep_n = max(4, int(total * ratio))
    top = sorted(scored, key=lambda x: x[2], reverse=True)[:keep_n]
    top_sorted = sorted(top, key=lambda x: x[0])
    return " ".join(s for _, s, _ in top_sorted)


def _paragraphise(text: str, target_para_words: int = 80) -> list[str]:
    """Split a flat text block into readable paragraphs."""
    sentences = _split_sentences(text)
    paragraphs = []
    current: list[str] = []
    word_count = 0
    for s in sentences:
        current.append(s)
        word_count += len(s.split())
        if word_count >= target_para_words:
            paragraphs.append(" ".join(current))
            current = []
            word_count = 0
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


def _extract_keywords(text: str, n: int = 5) -> list[str]:
    """Return the n most frequent non-stop words as keywords."""
    stop = {"the","a","an","is","are","was","were","be","been","being",
            "have","has","had","do","does","did","will","would","could",
            "should","may","might","shall","can","and","or","but","in",
            "on","at","to","for","of","with","by","from","as","into","it",
            "this","that","these","those","i","we","you","he","she","they",
            "my","your","his","her","our","their","what","how","when","where"}
    words = re.findall(r'\b[a-zA-Z]{4,}\b', text.lower())
    freq: dict = {}
    for w in words:
        if w not in stop:
            freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda x: x[1], reverse=True)[:n]]


def _split_into_sections(text: str, num_sections: int = 4) -> list[dict]:
    """
    Divide transcript into roughly equal sections, each with a heading
    derived from its dominant keywords.
    """
    sentences = _split_sentences(text)
    if not sentences:
        return [{"heading": "Transcript", "body": text}]
    chunk_size = max(1, len(sentences) // num_sections)
    sections = []
    for i in range(0, len(sentences), chunk_size):
        chunk = sentences[i:i + chunk_size]
        body = " ".join(chunk)
        kws = _extract_keywords(body, 3)
        heading = ", ".join(w.capitalize() for w in kws) if kws else f"Part {len(sections) + 1}"
        sections.append({"heading": heading, "body": body})
    return sections


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def process_transcripts(
    transcripts: list[dict],
    action: str,
    include_images: bool
) -> list[dict]:
    """
    Process a list of {title, text, video_url} into structured content dicts.

    Returns a list of content dicts shaped for formatter.py.
    action: "plain" | "summary" | "booklet" | "magazine"
    """
    results = []

    for t in transcripts:
        title = t["title"]
        raw = t["text"]
        url = t.get("video_url", "")

        if action == "plain":
            results.append({
                "type": "plain",
                "title": title,
                "video_url": url,
                "body": _clean_text(raw)
            })

        elif action == "summary":
            cleaned = _clean_text(raw)
            summary = _summarise(cleaned)
            results.append({
                "type": "summary",
                "title": title,
                "video_url": url,
                "body": summary,
                "word_count_original": len(raw.split()),
                "word_count_summary": len(summary.split())
            })

        elif action == "booklet":
            cleaned = _clean_text(raw)
            paragraphs = _paragraphise(cleaned)
            results.append({
                "type": "booklet",
                "title": title,
                "video_url": url,
                "paragraphs": paragraphs
            })

        elif action == "magazine":
            cleaned = _clean_text(raw)
            sections = _split_into_sections(cleaned, num_sections=4)
            # Optionally attach images
            if include_images:
                for sec in sections:
                    kws = _extract_keywords(sec["body"], 4)
                    img = fetch_image_for_section(kws, sec["heading"])
                    sec["image"] = img  # dict with url/caption, or None
            else:
                for sec in sections:
                    sec["image"] = None
            results.append({
                "type": "magazine",
                "title": title,
                "video_url": url,
                "sections": sections,
                "cover_keywords": _extract_keywords(cleaned, 5)
            })

    return results
