# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt   # setup
.venv/bin/python roofready_app.py                 # dashboard at http://127.0.0.1:5001 (--port to change)
.venv/bin/python -m unittest -v                   # all tests (offline; no API key needed)
.venv/bin/python -m unittest -v test_panel_plan   # one test module
.venv/bin/python -m unittest test_panel_plan.SomeTestCase.test_name   # one test
.venv/bin/python process_solar_layers.py --address "..." --radius 100 --unit meters --output outputs/my-search  # CLI pipeline
```

There is no build step or linter. The Google key comes from `GOOGLE_MAPS_API_KEY` (env var or the gitignored `.env`, read by `solar_address.local_api_key`). Live runs call Geocoding, Solar, and Places API (New), all of which are billable. The dashboard's **Try the sample area** button and `demo_roofready.py` use synthetic GeoTIFFs and make no API calls. The integration tests use those same GeoTIFFs.

## Architecture

The repo is a flat set of Python modules, a Flask app, and vanilla JS (`static/`, `templates/index.html`). `README.md` documents user-facing behavior and modeling assumptions in detail. Keep it in sync when behavior changes.

Pipeline, in dependency order:

1. **`solar_address.py`**: base module that every other module imports. It provides `InputError` (the shared validation exception, mapped to HTTP 400), HTTPS/certifi context, `get_json`, geocoding, lower-48/DC validation, and radius → Solar request parameters (radius limits and pixel sizes are in the README table).
2. **`process_solar_layers.py`**: `run_pipeline` downloads the Solar GeoTIFF layers concurrently. It reprojects them onto a local UTM metric grid (`metric_grid`, max 2,000 px per side) and masks them to the circle. It then detects roofs as connected components of Google's rooftop mask and ranks them by estimated annual energy. The output is a `result` dict.
3. **`roof_details.py`**: reverse-geocodes roof addresses (bounded concurrency, timeouts). `describe_results` generates the findings and next-steps text, and `save_report` writes `results.json`, CSV, GeoJSON, and `summary.txt`.
4. **`panel_plan.py`**: greedy placement of actual module rectangles into roof polygons, constrained by budget and energy target. It uses Shapely batch `covers` with prepared geometry, both orientations, 0.2 m gaps, and a 50,000-position bound.
5. **`panel_recommendations.py`**: loads `data/panel_catalog.csv`. It runs `propose_panels` per catalog model, sorts the plans into feasibility tiers and ranks them by priority, and applies roof include/exclude selection. With `reuse_comparison=True`, the ranked comparison is cached on a hash of its inputs, so a model preview recomputes only the selected layout.
6. **`installation_cost.py`**: regional $/W cost multiplier. **`solar_report.py`**: Pillow-rendered multi-page PDF (no reportlab).

The central data structure is the `result` dict. Each stage mutates it in place and it is persisted as `outputs/<job_id>/results.json`. When you add fields, account for older saved analyses: on startup the app reloads every folder in `outputs/` that contains `results.json`.

**`roofready_app.py`** uses a `create_app(output_root)` factory, which tests call with a temp directory. Jobs live in an in-memory dict guarded by a `Lock`. A single-worker `ThreadPoolExecutor` enforces one running analysis at a time, and the frontend polls `/api/jobs/<id>`. Replanning (`/api/plan`), roof-address lookup, and PDF export reuse saved results without new Solar requests. Security invariants to preserve:
- POST requests must carry a same-origin `Origin` header, and `TRUSTED_HOSTS` is limited to localhost.
- API keys are passed only as worker arguments. They are never stored in job state, results, or output files.
- `/results/<job>/<file>` serves only an allowlist of filenames.
