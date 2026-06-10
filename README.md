# YouTube Transcript Extractor

Extract transcripts from YouTube videos, channels, and playlists — then export as plain text, summary, booklet, or magazine layout.

---

## Run locally

**Requirements:** Python 3.10+

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start the app
python app.py

# 3. Open in your browser
# http://localhost:5000
```

---

## Deploy to Railway (free tier)

1. Create a free account at https://railway.app
2. Click **New Project → Deploy from GitHub repo** (push this folder to a GitHub repo first)
3. Railway auto-detects the `Procfile` and deploys. No config needed.
4. Your app gets a public URL like `https://your-app.up.railway.app`

Alternatively, use **Render** (https://render.com) — same process, also free.

---

## Features

- **Single video, channel, or playlist** extraction
- **Guaranteed N transcripts** mode — keeps scanning until it finds exactly the number you want
- **Scan limit** — prevents endlessly scanning large channels with few subtitled videos
- **4 output modes:** plain text, summary, booklet, magazine
- **Magazine mode** can include relevant images from Wikimedia Commons (skipped if nothing fits)
- **4 export formats:** Markdown, HTML, PDF, Word (.docx)
- **Languages:** English, German, Spanish, Persian/Farsi
- **Live progress panel** — shows scanned / extracted / failed counts in real time

---

## Project structure

```
app.py            Flask app + job management
extractor.py      YouTube transcript fetching (single / channel / playlist)
processor.py      Rule-based text processing (clean, summarise, booklet, magazine)
formatter.py      Output generation (MD, HTML, PDF, DOCX)
image_fetcher.py  Wikimedia Commons image search for magazine mode
templates/
  index.html      Single-page UI
```
