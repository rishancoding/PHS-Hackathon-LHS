# Address and radius → Google Solar API

## RoofReady dashboard — the complete pipeline

Run from this folder:

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python roofready_app.py
```

Open **http://127.0.0.1:5001** in your browser. Enter the street address, radius, and units, then click **Find roofs and panels**. The server uses your configured Google key; you can override it in the dashboard. Use **Try the sample area** to test without API requests. This demo is explicitly labeled and contains invented roofs and sunlight values.

### Address picker and loading screen

Start typing a street address; suggestions appear directly below the input. Use the up/down arrows and Enter, or click a result. Recent non-demo searches are also available as suggestions, and in **Open a saved analysis**, as plain street addresses (building or place names such as "Google Building 41" are dropped) so they can be searched again. Live suggestions use **Places API (New)**: enable it in the same Google Cloud project and allow it for your existing server key, in addition to Geocoding and Solar. The UI still accepts manual addresses when no key is configured or the service is unavailable. Lower-48/DC validation remains in the Geocoding step before analysis.

Autocomplete waits for at least three characters and 400 ms without another keystroke. Stale browser responses are discarded; predictions are not saved. The server calls Google over HTTPS with the key in a header and returns only address text to the picker. This uses per-request Autocomplete billing followed by the existing Geocoding call on Analyze, without a discounted-session claim. Google Maps attribution appears with live predictions. [Google Autocomplete documentation](https://developers.google.com/maps/documentation/places/web-service/place-autocomplete).

The SVG roof-and-panel logo is `static/roofready-mark.svg` and also serves as the favicon. Completed analyses show four selected-layout metrics above a map and panel summary. The original stacked input form stays below the map; it collapses after analysis and **Edit search** reopens it. Cost and target-coverage visuals use actual proposal values; unknown values remain unavailable. Other equipment and installation remain a combined estimate, and electricity-bill savings are not estimated. The panel table lists every eligible recommendation, with detailed warranties and alternative-use scenarios in a disclosure. Controls stack on smaller screens. **Find roofs and panels** opens a loading screen with real server stages and elapsed time; there is no fabricated percentage or completion time. **Continue in background** (or Escape) dismisses the screen while processing continues. Keyboard focus is restored when the screen closes, and reduced-motion settings disable the loading animation.

Roof selection is under **Roof candidates**, below the panel comparisons. Uncheck a roof to recompute every panel alternative and the current layout using only included roofs; excluded roofs stay visible in gray. The selection is saved with the analysis, and **Include all roofs** restores them. New address searches start with all detected roofs included. Roof-potential rankings and search coverage still describe the entire detected area.

**Side-by-side comparison** displays installed cost/range, annual energy, shortfall, module area, dimensions, and documented warranties under identical constraints. Select two different models, then preview either on the map. Comparison choices stay selected during replans; they are browser-session choices, not new saved analyses.

**Download PDF** exports a self-contained multi-page report with the annotated aerial map, chosen model and source URLs, costs, findings, next steps, roof selections, placement explanations, and the two comparison choices. It uses the existing Pillow dependency and embeds rendered pages; PDF text and URLs are not selectable/clickable. Downloading and comparing existing results make no Google API requests. JSON, CSV and GeoJSON exports remain available in their expandable sections.

**Why are panels placed here?** beside the map opens the placement explanation within **Methodology & assumptions**. Each roof selector also explains why its roof was used, excluded, or left without panels. Placement prioritizes roof-average annual yield, then geometric fit of the actual module in either axis-aligned orientation with 0.2 m gaps. It does not optimize sunlight at each individual panel location. The selected model’s specifications and costs are beside the map. Switching models updates the map and dashboard without automatically scrolling the page. Module specifications, detailed cost assumptions, other scenarios, and roof details remain expandable so all information stays available. Full-search potential is kept separate from the selected layout’s annual output.

The planner batches rectangle-fit checks through Shapely and reuses prepared roof geometry across catalog models, preserving the original grid positions, both orientations, 0.2 m gaps, and 50,000-position safety bound. Panel previews reuse the ranked comparison when the catalog, roofs, geographic estimate and constraints are unchanged; only the newly chosen layout is computed. Changes to budget, target, priority, area cap, roof selection, catalog, geometry or resolution invalidate that comparison.

The four raster downloads run concurrently. Roof addresses use at most four simultaneous requests, deduplicated by representative-point coordinates; resolved addresses are reused. An eight-second address-request timeout and failure stop prevent an unavailable service from blocking the whole address list. Unresolved addresses remain marked unavailable and can be retried with **Look up roof addresses**. Initial Geocoding/Solar request and download timeouts remain unchanged. Local completion polling uses 400 ms intervals. PDF text wraps by words and reuses fonts to reduce export time. Live Google latency still depends on the network and search area.

The dashboard executes these steps:

1. Geocode and validate the center address in the lower 48 states or DC.
2. Request regional imagery, rooftop mask, annual sunlight, and elevation from Google Solar.
3. Download GeoTIFFs, align them in a local UTM coordinate system measured in meters, and mask them to the requested circle.
4. Detect connected roof regions from Google's rooftop mask, then rank them by estimated annual solar energy.
5. Show aerial imagery with selectable roof outlines, a sunlight overlay, a ranked table, and CSV / GeoJSON / JSON downloads. Zoom with the controls and drag the image to pan.

Completed analyses are saved under `outputs/` (excluded from Git). Choose **Open a saved analysis** to reopen one after restarting the server. A single analysis runs at a time. Closing the browser does not cancel an active analysis. The server runs locally, with debug mode off. To stop it, press **Control+C** in the terminal. For another port, run `.venv/bin/python roofready_app.py --port 5002`.

### Budget, energy target, and proposed panels

Enter an installation **budget in USD** and an **annual energy target in kWh/year**. **Budget includes installation** is checked by default and applies the budget to the full estimated installed cost. Uncheck it to budget for module hardware only; other equipment and installation remain separately shown as additional costs. Changing the toggle on a completed analysis updates the layout and comparisons without new Google requests. The choice is saved in the analysis and included in the PDF. Set an optional **maximum panel area in m²** across all candidate roofs, or leave it blank to use detected roof space. Choose **Balanced**, **Lower installed cost**, **Use less roof space**, or **Longer documented warranty**. After an analysis, click **Update recommendations** to change constraints or preferences without new Google API requests.

### Catalog panel recommendations

`panel_recommendations.py` reads `panel_catalog (2).csv` on each comparison. It compares the 15 rigid modules and lists the five portable/flexible models separately because their permanent roof mounting is not modeled. Stock and shipping status do not affect comparisons. Actual CSV dimensions and STC rated watts determine module placement and annual output; advertised cell efficiency is not treated as full-module efficiency. Both axis-aligned orientations are tried with 0.2 m gaps. Counts respect polygon fit, usable fraction, sunlight coverage, budget, optional total module-area cap, and a 5,000-panel planning limit. A 50,000-position limit per orientation per roof bounds large searches; affected plans report the search limit. Panels may occupy several roofs within the radius. Each alternative uses a single panel model; mixed-model electrical designs are not optimized.

Recommendations appear in three tiers:

1. Meets the annual target within the central budget and roof-space constraints.
2. Fits some panels but falls short of the annual target.
3. Fits no panels under the current assumptions.

Constraint tier takes precedence over preference. Within an unmet tier, the smallest energy shortfall comes first. Feasible plans are ranked by installed cost for balanced/cost priorities, module area for space priority, or documented product warranty followed by output warranty for warranty priority. Unknown warranty lengths do not receive a warranty advantage. Reasons explain hardware cost per watt, power density, documented coverage, temperature coefficients, and small-module tradeoffs where supported. Product warranties and performance warranties cover different things; neither proves physical durability or actual service life. Unknown degradation remains unknown, and no bifacial rear gain is added.

The map appears first, with the chosen panel’s exact manufacturer/model name, description, and product link above it. The #1 recommendation is featured in **Ranked panel recommendations**; **View other panel suggestions** opens collapsed ranked rows with each model’s stats and a possible use scenario. The map, recommendations, plan/cost, overview, roof candidates, plotted-panel explanation, findings, and method sections can be collapsed. Click a recommendation row (or **Preview this panel on the map**) on an alternative to update the chosen-panel heading, blue rectangles, output, cost, findings, and exports. The selected model can differ from the top recommendation. JSON exports include all compared alternatives and reasons, the selected catalog snapshot, constraint inputs, exclusions, and the selected model's panel geometries and placed width/height in meters. Older saved analyses can generate recommendations by clicking **Update recommendations**.

The geographic cost model retains the Q1 2024 DOE/NREL $3.15/W residential benchmark in 2023 dollars and illustrative region multipliers: Northeast 1.12, Midwest 0.95, South 0.95, West 1.08. To reflect catalog price differences without adding module prices twice, the model uses **CSV module price + rated watts × (regional benchmark − $0.35/W)**. The $0.35/W reference module allowance is an explicit modeling assumption, not a sourced current price. Remaining cost includes other equipment and installation rather than labor alone. The ±25% total range and region multipliers are illustrative; no inflation adjustment is applied. These are screening estimates before incentives, excluding batteries, shipping and tax, not current local quotes. Budget feasibility uses the central estimate, so the upper estimate can exceed the budget. Commercial or multi-building costs may differ. [Benchmark source](https://www.nrel.gov/docs/fy25osti/92257.pdf).

Roof-potential rankings retain the separate panel-density assumption. Catalog recommendations use actual model wattage and layouts, so their ranking can differ from total roof potential. The map converts catalog meters to image pixels using the analysis resolution, so each blue footprint has the selected module’s dimensions, with a possible 90° rotation. The dimension caption and hover tooltip identify the model and footprint size. Clicking an alternative updates the saved plan and the aerial overlay together without another Google request, retaining zoom and sunlight settings. Rapid model clicks are serialized so the last requested model becomes the preview. The layout is a greedy screening search and does not guarantee a global optimum.

**Blue rectangles are proposed panels; green outlines are candidate roof regions.** Scroll or pinch to zoom, drag to pan, double-click to zoom in, or use the + / − / Reset controls. Use **Update recommendations** to replan a completed or saved analysis without new Google API calls. Proposal inputs, panel geometries in preview pixel coordinates, cost, and estimated energy are included in the JSON export; CSV and roof GeoJSON remain the regional roof-potential exports.

**Possible existing panels** are flagged with a color check, not a trained model: on detected roofs, it looks for slate-blue, mid-brightness areas at least about 1.8 m wide and 4 m², roughly rectangular, and (on 0.1 m imagery) showing the grid texture of module frames. Dark blue areas are treated as shadows. Flagged areas appear as orange dashed shapes on the map and PDF, and new panels avoid them with the usual 0.2 m gap. Findings, roof notes and the CSV (`existing_panel_area_m2`) report them. Rooftop equipment or blue roofing can be flagged by mistake, and black or very dark panels can be missed, so verify against the aerial image; exclude a roof if needed. Thresholds are in `existing_panels.py` and were tuned on Google Solar imagery of Mountain View, CA. Analyses saved before this check have no flags until searched again. The sample area includes one synthetic existing array.

Other obstructions are not detected or excluded. Placement uses the displayed roof polygon and roof-average solar yield, so it does not resolve individual panel shading, roof pitch, fire setbacks, ownership, or structural readiness. The circles and panels are conceptual screening overlays, not an installation design.

### Command-line pipeline

```sh
.venv/bin/python process_solar_layers.py --address "1600 Amphitheatre Parkway, Mountain View, CA 94043" --radius 100 --unit meters --output outputs/my-search
```

This produces the original four TIFF layers, circle-masked TIFFs, aerial and sunlight preview PNGs, `results.json`, `roofs.geojson`, and `roofs.csv`. Restart the dashboard to open this CLI analysis from the saved-analysis menu. The pipeline requests only imagery and annual flux; monthly/hourly layers are not used for ranking. URLs can expire, so download immediately after requesting them. Keys and authenticated URLs are not written to result files.

### Detection and scoring

Roof rows and map tooltips use street addresses from Google reverse geocoding at a point inside each roof polygon. Matches whose Google coordinate falls outside the roof are labeled **nearby / approximate**. Several roof sections can share an address; region IDs remain available to distinguish them. Unresolved addresses remain explicitly unavailable. Address lookup is a separate step: the main analysis finds roofs and places panels without it, and roofs show **Address not looked up**. After an analysis (new or saved) finishes, click **Look up roof addresses** to label its roofs using that analysis's roof outlines, without downloading the solar layers again. This makes one potentially billable Geocoding API call per roof location. Demo addresses are synthetic; lookup failures preserve the roof analysis.

**Search findings** gives a brief search-specific paragraph with the candidate count, radius, coverage, strongest region, and proposed panel cost and energy output. **Next steps** explains how to validate the roofs and proceed, including adjusting the budget or target when there is a shortfall. These paragraphs refresh when you update the plan and are included in `results.json` and `summary.txt`. Address fields are also included in CSV and GeoJSON exports. Reverse-geocoded matches do not establish parcel ownership or prove a building-to-address relationship.

The working detector uses **Google's rooftop mask plus connected components**, not a newly trained ML model. Touching roofs may merge, disconnected sections may split, and regions smaller than 10 m² are filtered out. Candidate IDs identify regions, not postal addresses or verified buildings. Roofs reaching the circle or raster boundary are flagged as partial. Areas represent the horizontal footprint inside the search radius, not the full sloped roof surface.

Google annual flux is measured in **kWh/kW/year**. The screening estimate is:

```text
estimated capacity (kW) = sunlight-covered footprint (m²) × usable fraction × panel capacity (kW/m²)
estimated energy (kWh/year) = estimated capacity × average annual yield (kWh/kW/year)
```

Defaults are **65% usable area** and **0.20 kW/m²**; edit them before analysis under **Estimation assumptions**. These are explicit assumptions, not engineering recommendations. Missing and negative sunlight values are excluded; no sunlight means unavailable energy, not zero potential. Coverage is reported per roof and for the search area. DSM supplies mean elevation, not a verified slope or roof-condition assessment. Google's flux already reflects its sunlight analysis; this code does not independently model shade or module losses. Ranking uses estimated energy, not structural readiness, pricing, payback, or installation approval.

Processing uses a common grid with at most 2,000 pixels per side to limit memory. The reported analysis resolution may be coarser than the source raster; small roofs can be lost at coarse resolutions. Google coverage and the original imagery date affect result completeness. This tool restricts the center address, not every pixel of a circle crossing a national border.

Run all tests with:

```sh
.venv/bin/python -m unittest -v
```

The integration tests use actual synthetic GeoTIFFs to check reprojection, missing sunlight, clipped boundaries, scoring units, empty areas, exports, and local dashboard jobs. A separately trained roof segmentation model needs a chosen model, licensed weights, and representative validation data; it is a future replacement for the current detector.

## Original address tool

Requires Python 3.9+. For reliable HTTPS certificates, install the certificate package in a local environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

The script finds this environment's certificate bundle even when run with `python3 solar_address.py`.

Run in this folder:

```sh
python3 solar_address.py
```

Enter a complete street address, radius, and `feet` or `meters`. The script reads your local testing key from the ignored `.env` file (`GOOGLE_MAPS_API_KEY=your-key`), so no key prompt is needed. Set `GOOGLE_MAPS_API_KEY` in your environment to override it. Never commit `.env`. Enable the **Geocoding API** in your Google Cloud project, and enable **Solar API** if you want to fetch solar data. Google Maps Platform requires billing; API calls may incur charges.

You can also supply the inputs as arguments:

```sh
python3 solar_address.py --address "1600 Amphitheatre Parkway, Mountain View, CA 94043" --radius 300 --unit feet
```

Output is JSON with the resolved address, latitude, longitude, radius and diameter in both units, API parameters, and a copyable Solar request URL. Replace `YOUR_API_KEY` in that URL with your key. Add `--fetch-solar` to request Solar data directly and include the response under `solar_data`:

```sh
python3 solar_address.py --address "1600 Amphitheatre Parkway, Mountain View, CA 94043" --radius 100 --unit meters --fetch-solar
```

The address must resolve to the lower 48 states or Washington, DC. Alaska, Hawaii, US territories, and foreign addresses return **Not allowed**. Partial or ambiguous matches are rejected; inspect the resolved address in the output. Validation uses Google's country and state address components, rather than a rectangular US bounding box. The restriction applies to the center address; a radius near a border can extend outside the US.

A radius measures distance **from the center**, so a 300-foot radius spans a 600-foot diameter. One foot is exactly 0.3048 meters. Pass `radiusMeters` to Google's `dataLayers:get` endpoint to request the region around the coordinates; `buildingInsights:findClosest` only returns a nearby building.

Google's single-request limits:

| Radius in meters | Pixel size chosen | Layers requested |
| --- | --- | --- |
| 0 < radius ≤ 100 | 0.1 m | All layers |
| 100 < radius ≤ 175 | 0.25 m | All layers |
| 175 < radius ≤ 250 | 0.25 m | Imagery and annual flux |
| 250 < radius ≤ 500 | 0.5 m | Imagery and annual flux |
| 500 < radius ≤ 1,000 | 1 m | Imagery and annual flux |

Larger radii are rejected, not reduced. Covering them needs multiple requests and combining their raster data. Monthly flux and hourly shade are unavailable above 175 meters in one request. Solar coverage is not guaranteed for every valid US address; Google can return `NOT_FOUND` when HIGH-quality data is unavailable. Returned data consists of metadata and URLs to GeoTIFF layers, not a list of every building or a computed circular-area summary. To analyze exactly a circle, download and mask the rasters using the requested center and radius.

Use from Python:

```python
from solar_address import prepare_request

result = prepare_request(
    "1600 Amphitheatre Parkway, Mountain View, CA 94043",
    radius=300,
    unit="feet",
    api_key="YOUR_API_KEY",
)
print(result["solar_parameters"])
```

Run offline tests with `python3 -m unittest -v`. Live calls require your API key.

References: [Geocoding API](https://developers.google.com/maps/documentation/geocoding/guides-v3/requests-geocoding), [Solar dataLayers parameters and limits](https://developers.google.com/maps/documentation/solar/reference/rest/v1/dataLayers/get), [Solar data layers](https://developers.google.com/maps/documentation/solar/data-layers).

New searches retain the state from Google Geocoding. Older saved searches infer the state from the resolved address when possible; otherwise the national baseline is used. Click Update budget / target to apply the automatic estimate to an older plan.
