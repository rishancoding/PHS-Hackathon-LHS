"""Local PhotoGrid dashboard. Run with .venv/bin/python roofready_app.py."""

import argparse
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import math
import json
import os
from pathlib import Path
import secrets
from threading import Lock
import time
import traceback

from flask import Flask, abort, jsonify, render_template, request, send_from_directory, send_file
from werkzeug.middleware.proxy_fix import ProxyFix

from address_autocomplete import address_predictions
from demo_roofready import run_demo
from process_solar_layers import run_pipeline
from solar_address import DEFAULT_API_KEY, InputError, radius_in_meters, search_address
from panel_plan import load_sunlight
from panel_recommendations import build_recommendations, validate_recommendation_inputs, load_catalog
from solar_report import create_pdf_report
from roof_details import add_roof_addresses, describe_results, save_report

ROOT = Path(__file__).resolve().parent


def resolve_key(supplied=""):
    """A key typed in the dashboard wins; otherwise the environment, then the local .env file."""
    return (supplied or "").strip() or os.environ.get("GOOGLE_MAPS_API_KEY", DEFAULT_API_KEY).strip()


def create_app(output_root=None):
    app = Flask(__name__)
    render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
    trusted_hosts = ["localhost", "127.0.0.1"]
    if render_host:
        trusted_hosts.append(render_host)
        # Render terminates HTTPS before forwarding to Gunicorn. Trust its one
        # scheme header; keep host validation and the same-origin POST check.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1, x_host=0, x_port=0, x_prefix=0)
    app.config.update(MAX_CONTENT_LENGTH=16384, TRUSTED_HOSTS=trusted_hosts)
    output_root = Path(output_root or os.environ.get("PHOTOGRID_OUTPUT_ROOT") or ROOT / "outputs")
    jobs = {}
    lock = Lock()
    executor = ThreadPoolExecutor(max_workers=1)
    # Previously completed CLI/dashboard runs can be opened without another API call.
    if output_root.exists():
        # Oldest first, so the saved-search list (newest first) reflects real run order.
        saved = sorted(
            (folder for folder in output_root.iterdir() if (folder / "results.json").is_file()),
            key=lambda folder: (folder / "results.json").stat().st_mtime,
        )
        for folder in saved:
            try:
                result = json.loads((folder / "results.json").read_text())
                if "roofs" in result and "summary" in result and "request" in result:
                    describe_results(result)
                    result.setdefault(
                        "completed_at",
                        time.strftime("%Y-%m-%dT%H:%M", time.localtime((folder / "results.json").stat().st_mtime)),
                    )
                    jobs[folder.name] = {"status": "complete", "message": "Saved analysis loaded", "result": result}
            except (ValueError, OSError):
                continue

    sunlight_cache = {}

    def sunlight_for(job_id):
        # Per-pixel sunlight for sunlight-aware placement; small cache so re-plans stay instant.
        if job_id not in sunlight_cache:
            if len(sunlight_cache) >= 3:
                sunlight_cache.pop(next(iter(sunlight_cache)))
            sunlight_cache[job_id] = load_sunlight(output_root / job_id)
        return sunlight_cache[job_id]

    @app.before_request
    def check_origin():
        # Local app: prevent unrelated sites from triggering billable requests.
        if request.method == "POST" and request.headers.get("Origin") != request.host_url.rstrip("/"):
            abort(403)

    @app.get("/")
    @app.get("/photogrid")
    def index():
        key = resolve_key()
        return render_template("index.html", configured=bool(key), hosted=bool(render_host))

    @app.get("/health")
    def health():
        return jsonify(status="ok", app="PhotoGrid")

    @app.post("/api/address-suggestions")
    def suggest_addresses():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("query"), str):
            return jsonify(error="Enter an address search."), 400
        query = body["query"].strip()
        if not 3 <= len(query) <= 200:
            return jsonify(error="Type between 3 and 200 characters."), 400
        supplied = body.get("api_key", "")
        if not isinstance(supplied, str):
            return jsonify(error="API key must be text."), 400
        with lock:
            saved = (
                search_address(job["result"]["request"])
                for job in reversed(list(jobs.values()))
                if job["status"] == "complete" and not job["result"].get("demo")
            )
            recent = list(dict.fromkeys(address for address in saved if query.casefold() in address.casefold()))[:5]
        fallback = [
            {"address": address, "label": address, "detail": "Previously analyzed", "source": "recent"}
            for address in recent
        ]
        key = resolve_key(supplied)
        if not key:
            return jsonify(
                suggestions=fallback, notice="Type a full address, or configure your Google key for live suggestions."
            )
        try:
            suggestions = address_predictions(query, key)
            return jsonify(
                suggestions=suggestions or fallback,
                notice="" if suggestions or fallback else "No matches. Try adding your city or ZIP code.",
            )
        except InputError as exc:
            return jsonify(suggestions=fallback, notice=str(exc))

    def execute(job_id, body, key):
        def progress(message):
            with lock:
                jobs[job_id]["message"] = message

        started = time.monotonic()
        try:
            directory = output_root / job_id
            if body.get("demo") is True:
                result = run_demo(directory, progress)
            else:
                result = run_pipeline(
                    body["address"],
                    body["radius"],
                    body["unit"],
                    key,
                    directory,
                    progress=progress,
                    usable_fraction=body["usable_fraction"],
                    panel_kw_per_m2=body["panel_kw_per_m2"],
                    saved_root=output_root,
                    reuse_imagery=body.get("reuse_imagery") is not False,
                )
            progress("Comparing panel models and building your layout…")
            build_recommendations(result, **body["plan_inputs"], sunlight=load_sunlight(directory))
            result["search_seconds"] = round(time.monotonic() - started, 1)
            result["completed_at"] = time.strftime("%Y-%m-%dT%H:%M")
            save_report(result, directory)
            with lock:
                jobs[job_id].update(status="complete", message="Analysis complete", result=result)
        except Exception as exc:
            message = str(exc).replace(key, "[redacted]") if key else str(exc)
            if not isinstance(exc, InputError):
                # Full traceback in the terminal for debugging; the key never reaches the log.
                details = traceback.format_exc()
                app.logger.error(
                    "Analysis %s failed:\n%s", job_id, details.replace(key, "[redacted]") if key else details
                )
            # Unexpected errors can include local paths or download URLs; show a stable message.
            if not isinstance(exc, InputError):
                message = "Raster processing failed. Check the downloaded TIFFs or try a smaller radius."
            with lock:
                jobs[job_id].update(status="failed", message=message)

    @app.post("/api/analyze")
    def analyze():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error="Expected a JSON request."), 400
        key = resolve_key()
        try:
            body["plan_inputs"] = validate_recommendation_inputs(body)
            load_catalog()
            if body.get("demo") is not True:
                supplied = body.get("api_key", "")
                if not isinstance(supplied, str):
                    raise InputError("API key must be text.")
                key = supplied.strip() or key
                if not key:
                    raise InputError("Enter a Google API key or set GOOGLE_MAPS_API_KEY.")
                if not isinstance(body.get("address"), str) or not body["address"].strip():
                    raise InputError("Enter a complete street address.")
                if body.get("unit") not in ("feet", "meters"):
                    raise InputError("Choose feet or meters.")
                body["radius"] = float(body.get("radius", 0))
                radius_in_meters(body["radius"], body["unit"])
                for field, default in (("usable_fraction", 0.65), ("panel_kw_per_m2", 0.2)):
                    body[field] = float(body.get(field, default))
                    if not math.isfinite(body[field]) or not 0 < body[field] <= 1:
                        raise InputError("Roof fraction and panel density must be greater than 0 and at most 1.")
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400
        # Keep credentials solely in the worker's arguments, never job state or output.
        body.pop("api_key", None)
        with lock:
            if any(job["status"] == "running" for job in jobs.values()):
                return jsonify(error="An analysis is already running. Wait for it to finish."), 409
            job_id = secrets.token_hex(12)
            jobs[job_id] = {"status": "running", "message": "Preparing analysis…"}
        executor.submit(execute, job_id, body, key)
        return jsonify(job_id=job_id), 202

    @app.get("/api/jobs/<job_id>")
    def status(job_id):
        with lock:
            job = jobs.get(job_id)
            if job is None:
                abort(404)
            return jsonify(job)

    @app.get("/api/history")
    def history():
        # Newest first; one entry per address and radius.
        runs, seen = [], set()
        with lock:
            for job_id, job in reversed(list(jobs.items())):
                if job["status"] != "complete":
                    continue
                result = job["result"]
                address = search_address(result["request"])
                identity = (address, round(result["request"].get("radius_meters", 0), 1), result.get("demo", False))
                if identity in seen:
                    continue
                seen.add(identity)
                runs.append(
                    {
                        "job_id": job_id,
                        "address": address,
                        "demo": result.get("demo", False),
                        "radius_meters": result["request"].get("radius_meters"),
                        "completed_at": result.get("completed_at"),
                    }
                )
        return jsonify(runs)

    @app.post("/api/plan/<job_id>")
    def replan(job_id):
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error="Expected plan inputs."), 400
        try:
            inputs = validate_recommendation_inputs(body)
        except InputError as exc:
            return jsonify(error=str(exc)), 400
        with lock:
            job = jobs.get(job_id)
            if not job or job["status"] != "complete":
                abort(404)
            result = dict(job["result"])
        try:
            build_recommendations(result, **inputs, reuse_comparison=True, sunlight=sunlight_for(job_id))
        except InputError as exc:
            return jsonify(error=str(exc)), 400
        save_report(result, output_root / job_id)
        with lock:
            jobs[job_id]["result"] = result
        return jsonify(result)

    @app.get("/api/report/<job_id>")
    def pdf_report(job_id):
        with lock:
            job = jobs.get(job_id)
            if not job or job["status"] != "complete":
                abort(404)
            result = deepcopy(job["result"])
        try:
            models = request.args.getlist("model")
            output = create_pdf_report(result, output_root / job_id, models)
        except InputError as exc:
            return jsonify(error=str(exc)), 400
        return send_file(
            output, mimetype="application/pdf", as_attachment=True, download_name=f"photogrid-{job_id}.pdf", max_age=0
        )

    @app.post("/api/addresses/<job_id>")
    def resolve_saved_addresses(job_id):
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict) or not isinstance(body.get("api_key", ""), str):
            return jsonify(error="API key must be text."), 400
        key = resolve_key(body.get("api_key", ""))
        with lock:
            job = jobs.get(job_id)
            if not job or job["status"] != "complete":
                abort(404)
            if not key and not job["result"].get("demo"):
                return jsonify(error="Enter a Google API key for address lookup."), 400
            if any(j["status"] == "running" for j in jobs.values()):
                return jsonify(error="Wait for the current analysis to finish."), 409
            result = json.loads(json.dumps(job["result"]))
            job.update(status="running", message="Looking up roof addresses…")

        def resolve():
            def progress(message):
                with lock:
                    jobs[job_id]["message"] = message

            try:
                add_roof_addresses(result, key, progress)
                save_report(result, output_root / job_id)
                message = "Roof address lookup complete"
            except Exception:
                details = traceback.format_exc()
                app.logger.error(
                    "Address lookup for %s failed:\n%s", job_id, details.replace(key, "[redacted]") if key else details
                )
                message = "Address lookup could not finish; previous results retained."
                with lock:
                    result.update(jobs[job_id]["result"])
            with lock:
                jobs[job_id].update(status="complete", message=message, result=result)

        executor.submit(resolve)
        return jsonify(job_id=job_id), 202

    @app.get("/results/<job_id>/<filename>")
    def artifact(job_id, filename):
        with lock:
            if job_id not in jobs or jobs[job_id]["status"] != "complete":
                abort(404)
        if filename not in {
            "aerial.png",
            "sunlight.png",
            "aerial.jpg",
            "sunlight.webp",
            "roofs.csv",
            "roofs.geojson",
            "results.json",
            "summary.txt",
            "clipped_roof_mask.tif",
            "clipped_annual_flux.tif",
            "clipped_dsm.tif",
        }:
            abort(404)
        return send_from_directory(output_root / job_id, filename, as_attachment=not filename.endswith(".png"))

    app.extensions["roofready_executor"] = executor
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5001)
    args = parser.parse_args()
    create_app().run(host="127.0.0.1", port=args.port, debug=False)
