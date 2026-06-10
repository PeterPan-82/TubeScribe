import os
import uuid
import json
import threading
import time
from flask import Flask, render_template, request, Response, send_file, jsonify
from extractor import extract_single, extract_multi
from processor import process_transcripts
from formatter import format_output

app = Flask(__name__)
app.config["OUTPUT_DIR"] = os.path.join(os.path.dirname(__file__), "outputs")
os.makedirs(app.config["OUTPUT_DIR"], exist_ok=True)

# In-memory job store  { job_id: { status, scanned, extracted, failed, target, log, output_path, error } }
jobs: dict = {}
jobs_lock = threading.Lock()


def update_job(job_id, **kwargs):
    with jobs_lock:
        jobs[job_id].update(kwargs)


def run_job(job_id, params):
    try:
        update_job(job_id, status="running")
        source = params["source"]          # "single" | "channel" | "playlist"
        url = params["url"]
        scope = params.get("scope", "all") # "all" | "latest" | "guaranteed"
        n = int(params.get("n", 20))
        scan_limit_type = params.get("scan_limit_type", "multiplier")
        scan_limit_mult = int(params.get("scan_limit_mult", 3))
        scan_limit_abs = int(params.get("scan_limit_abs", 200))
        action = params["action"]          # "plain" | "summary" | "booklet" | "magazine"
        fmt = params["fmt"]                # "md" | "html" | "pdf" | "docx"
        include_images = params.get("include_images", False)
        languages = params.get("languages", ["en", "de", "es", "fa"])

        transcripts = []  # list of { title, text, video_url }

        def progress_cb(scanned, extracted, failed, target, video_title, success):
            update_job(job_id,
                       scanned=scanned,
                       extracted=extracted,
                       failed=failed,
                       target=target)
            log_entry = {
                "title": video_title,
                "status": "ok" if success else "fail",
                "ts": time.time()
            }
            with jobs_lock:
                jobs[job_id]["log"].append(log_entry)

        if source == "single":
            result = extract_single(url, languages)
            if result:
                transcripts.append(result)
                update_job(job_id, scanned=1, extracted=1, failed=0, target=1)
                with jobs_lock:
                    jobs[job_id]["log"].append({"title": result["title"], "status": "ok", "ts": time.time()})
            else:
                update_job(job_id, scanned=1, extracted=0, failed=1, target=1)
                with jobs_lock:
                    jobs[job_id]["log"].append({"title": url, "status": "fail", "ts": time.time()})
        else:
            # channel or playlist
            if scope == "guaranteed":
                if scan_limit_type == "multiplier":
                    limit = n * scan_limit_mult
                else:
                    limit = scan_limit_abs
                target = n
            elif scope == "latest":
                limit = n
                target = n
            else:
                limit = None
                target = None

            transcripts = extract_multi(
                url=url,
                source_type=source,
                scope=scope,
                target_n=n if scope in ("latest", "guaranteed") else None,
                scan_limit=limit,
                languages=languages,
                progress_cb=progress_cb
            )

        if not transcripts:
            update_job(job_id, status="done", error="No transcripts could be extracted.")
            return

        # Process
        processed = process_transcripts(transcripts, action, include_images)

        # Format & save
        ext_map = {"md": "md", "html": "html", "pdf": "pdf", "docx": "docx"}
        ext = ext_map.get(fmt, "md")
        filename = f"transcripts_{job_id[:8]}.{ext}"
        out_path = os.path.join(app.config["OUTPUT_DIR"], filename)
        format_output(processed, fmt, out_path, action)

        update_job(job_id, status="done", output_path=out_path, filename=filename)

    except Exception as e:
        update_job(job_id, status="error", error=str(e))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/start", methods=["POST"])
def start():
    params = request.get_json(force=True)
    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "status": "queued",
            "scanned": 0,
            "extracted": 0,
            "failed": 0,
            "target": None,
            "log": [],
            "output_path": None,
            "filename": None,
            "error": None
        }
    t = threading.Thread(target=run_job, args=(job_id, params), daemon=True)
    t.start()
    return jsonify({"job_id": job_id})


@app.route("/progress/<job_id>")
def progress(job_id):
    def stream():
        while True:
            with jobs_lock:
                job = jobs.get(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'job not found'})}\n\n"
                break
            payload = {
                "status": job["status"],
                "scanned": job["scanned"],
                "extracted": job["extracted"],
                "failed": job["failed"],
                "target": job["target"],
                "log": job["log"][-20:],  # last 20 entries
                "filename": job["filename"],
                "error": job["error"]
            }
            yield f"data: {json.dumps(payload)}\n\n"
            if job["status"] in ("done", "error"):
                break
            time.sleep(0.8)
    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/download/<job_id>")
def download(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job or not job.get("output_path"):
        return "File not found", 404
    return send_file(job["output_path"], as_attachment=True,
                     download_name=job["filename"])


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
