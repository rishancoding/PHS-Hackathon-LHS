# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt   # setup
.venv/bin/python roofready_app.py                 # PhotoGrid at http://127.0.0.1:5001/photogrid (--port to change)
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
2. **`process_solar_layers.py`**: `run_pipeline` reuses saved layers from an earlier run with the same center, radius and pixel size (`find_saved_layers`), otherwise requests HIGH-quality layers (falling back once to MEDIUM) and downloads the GeoTIFFs concurrently. It reprojects them onto a local UTM metric grid (`metric_grid`, max 2,000 px per side) and masks them to the circle. It then detects roofs as connected components of Google's rooftop mask and ranks them by estimated annual energy. The output is a `result` dict.
3. **`roof_details.py`**: reverse-geocodes roof addresses (bounded concurrency, timeouts). `describe_results` generates the findings and next-steps text, and `save_report` writes `results.json`, CSV, GeoJSON, and `summary.txt`.
4. **`panel_plan.py`**: greedy placement of actual module rectangles into roof polygons, constrained by budget and energy target. Grids are rotated to each roof's minimum rotated rectangle, existing-panel areas and user `blocked_zones` are subtracted, and with per-pixel sunlight (`load_sunlight`, summed-area tables from `clipped_annual_flux.tif`) the sunniest 100 kWh/kW bands fill first. Energy uses `system_derate` (default 0.85). It uses Shapely batch `covers`, both orientations, 0.2 m gaps, and a 50,000-position bound. `existing_panels.py` is the color-based existing-panel detector run inside `process_layers`.
5. **`panel_recommendations.py`**: loads `data/panel_catalog.csv`. It runs `propose_panels` per catalog model, sorts the plans into feasibility tiers and ranks them by priority, and applies roof include/exclude selection. With `reuse_comparison=True`, the ranked comparison is cached on a hash of its inputs, so a model preview recomputes only the selected layout.
6. **`installation_cost.py`**: regional $/W cost multiplier. **`impact.py`**: bill savings, payback, CO₂ and homes from sourced constants (EIA, EPA eGRID). **`solar_report.py`**: Pillow-rendered multi-page PDF (no reportlab).
7. **`project_store.py`**: lazy PyMongo connection and named snapshots in Atlas. `/api/projects` saves/lists snapshots; POST `/api/projects/<id>/open` restores one into a fresh local job. Stored imagery references point to the original output folder; JSON/CSV exports belong to the fresh job. Missing assets allow viewing the saved layout but block re-planning. Tests inject `project_store=False` or an in-memory collection; automated tests must never use the real Atlas database.
8. **`solar_advisor.py`**: DeepSeek chat (`deepseek-v4-flash`, OpenAI-compatible `/chat/completions` via urllib). POST `/api/advisor/<job_id>` takes the browser's recent messages; the server builds the system prompt from the job's current `result` (`site_data`: site, settings, selected layout, impact, ranked models, full catalog, roofs) plus `METHODS`, which restates the formulas in `panel_plan`, `panel_recommendations`, `installation_cost` and `impact`. Update `METHODS` when those change. The `run_scenario` tool reruns `build_recommendations` on a deep copy, so the job's result is never modified. `static/advisor.js` renders model text as DOM nodes, never `innerHTML`. Tests inject `advisor=False` or a `SolarAdvisor` with a fake `transport`; automated tests must never call DeepSeek.

The central data structure is the `result` dict. Each stage mutates it in place and it is persisted as `outputs/<job_id>/results.json`. When you add fields, account for older saved analyses: on startup the app reloads every folder in `outputs/` that contains `results.json`.

**`roofready_app.py`** uses a `create_app(output_root)` factory, which tests call with a temp directory. Jobs live in an in-memory dict guarded by a `Lock`. A single-worker `ThreadPoolExecutor` enforces one running analysis at a time, and the frontend polls `/api/jobs/<id>`. Replanning (`/api/plan`), roof-address lookup, and PDF export reuse saved results without new Solar requests. Security invariants to preserve:
- POST requests must carry a same-origin `Origin` header. `TRUSTED_HOSTS` accepts localhost and, when deployed, the exact `RENDER_EXTERNAL_HOSTNAME`. Only Render mode trusts one forwarded scheme header; forwarded host headers remain untrusted. See `docs/DEPLOYMENT.md` and `render.yaml` for the shared demo setup.
- API keys are passed only as worker arguments. They are never stored in job state, results, or output files. `DEEPSEEK_API_KEY` stays inside `SolarAdvisor` and never reaches the browser or an error message.
- `/results/<job>/<file>` serves only an allowlist of filenames.
