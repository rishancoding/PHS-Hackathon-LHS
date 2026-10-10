"""Local RoofReady dashboard. Run with .venv/bin/python roofready_app.py."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import math
import json
import os
from pathlib import Path
import secrets
from threading import Lock

from flask import Flask, abort, jsonify, render_template, request, send_from_directory

from demo_roofready import run_demo
from process_solar_layers import run_pipeline
from solar_address import DEFAULT_API_KEY, InputError, radius_in_meters
from panel_recommendations import build_recommendations, validate_recommendation_inputs, load_catalog
from roof_details import add_roof_addresses, describe_results, save_report

ROOT = Path(__file__).resolve().parent


def create_app(output_root=None):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=16384, TRUSTED_HOSTS=["localhost", "127.0.0.1"])
    output_root = Path(output_root or ROOT / "outputs")
    jobs = {}
    lock = Lock()
    executor = ThreadPoolExecutor(max_workers=1)
    # Previously completed CLI/dashboard runs can be opened without another API call.
    if output_root.exists():
        for folder in output_root.iterdir():
            if not folder.is_dir() or not (folder / "results.json").is_file():
                continue
            try:
                result = json.loads((folder / "results.json").read_text())
                if "roofs" in result and "summary" in result and "request" in result:
                    describe_results(result)
                    jobs[folder.name] = {"status": "complete", "message": "Saved analysis loaded", "result": result}
            except (ValueError, OSError):
                continue

    @app.before_request
    def check_origin():
        # Local app: prevent unrelated sites from triggering billable requests.
        if request.method == "POST" and request.headers.get("Origin") != request.host_url.rstrip("/"):
            abort(403)

    @app.get("/")
    def index():
        key = os.environ.get("GOOGLE_MAPS_API_KEY", DEFAULT_API_KEY).strip()
        return render_template("index.html", configured=bool(key))

    def execute(job_id, body, key):
        def progress(message):
            with lock:
                jobs[job_id]["message"] = message
        try:
            directory = output_root / job_id
            if body.get("demo") is True:
                result = run_demo(directory, progress)
                add_roof_addresses(result)
            else:
                result = run_pipeline(body["address"], body["radius"], body["unit"], key,
                                      directory, progress=progress, usable_fraction=body["usable_fraction"],
                                      panel_kw_per_m2=body["panel_kw_per_m2"])
            build_recommendations(result, **body["plan_inputs"])
            save_report(result, directory)
            with lock:
                jobs[job_id].update(status="complete", message="Analysis complete", result=result)
        except Exception as exc:
            message = str(exc).replace(key, "[redacted]") if key else str(exc)
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
        key = os.environ.get("GOOGLE_MAPS_API_KEY", DEFAULT_API_KEY).strip()
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
                for field, default in (("usable_fraction", .65), ("panel_kw_per_m2", .2)):
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
        with lock:
            return jsonify([
                {"job_id": job_id, "address": job["result"]["request"]["address"], "demo": job["result"].get("demo", False)}
                for job_id, job in reversed(list(jobs.items())) if job["status"] == "complete"
            ])

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
            build_recommendations(result, **inputs)
        except InputError as exc:
            return jsonify(error=str(exc)), 400
        save_report(result, output_root / job_id)
        with lock:
            jobs[job_id]["result"] = result
        return jsonify(result)

    @app.post("/api/addresses/<job_id>")
    def resolve_saved_addresses(job_id):
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict) or not isinstance(body.get("api_key", ""), str):
            return jsonify(error="API key must be text."), 400
        key = body.get("api_key", "").strip() or os.environ.get("GOOGLE_MAPS_API_KEY", DEFAULT_API_KEY).strip()
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
        if filename not in {"aerial.png", "sunlight.png", "roofs.csv", "roofs.geojson", "results.json", "summary.txt",
                            "clipped_roof_mask.tif", "clipped_annual_flux.tif", "clipped_dsm.tif"}:
            abort(404)
        return send_from_directory(output_root / job_id, filename,
                                   as_attachment=not filename.endswith(".png"))

    app.extensions["roofready_executor"] = executor
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5001)
    args = parser.parse_args()
    create_app().run(host="127.0.0.1", port=args.port, debug=False)
