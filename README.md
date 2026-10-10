# Address and radius → Google Solar API

## RoofReady dashboard — the complete pipeline

Run from this folder:

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python roofready_app.py
```

Open **http://127.0.0.1:5001** in your browser. Enter the street address, radius, and units, then click **Analyze rooftops**. The server uses your configured Google key; you can override it in the dashboard. Use **Explore a synthetic demo** to test without API requests. This demo is explicitly labeled and contains invented roofs and sunlight values.

The dashboard executes these steps:

1. Geocode and validate the center address in the lower 48 states or DC.
2. Request regional imagery, rooftop mask, annual sunlight, and elevation from Google Solar.
3. Download GeoTIFFs, align them in a local UTM coordinate system measured in meters, and mask them to the requested circle.
4. Detect connected roof regions from Google's rooftop mask, then rank them by estimated annual solar energy.
5. Show aerial imagery with selectable roof outlines, a sunlight overlay, a ranked table, and CSV / GeoJSON / JSON downloads. Zoom with the controls and drag the image to pan.

Completed analyses are saved under `outputs/` (excluded from Git). Choose **Open a saved analysis** to reopen one after restarting the server. A single analysis runs at a time. Closing the browser does not cancel an active analysis. The server runs locally, with debug mode off. To stop it, press **Control+C** in the terminal. For another port, run `.venv/bin/python roofready_app.py --port 5002`.

### Budget, energy target, and proposed panels

Enter an installation **budget in USD**, an **annual energy target in kWh/year**, and an editable assumed **installed cost in USD/watt**. The $3/W default is an illustrative planning assumption, not a market quote. The planner fills candidate roof regions in order of their average annual solar yield, placing conceptual 1 × 2 m panels with 0.2 m gaps. Module power follows the panel-density assumption (0.4 kW per panel at the default). It limits counts by usable roof fraction, sunlight coverage, available geometric space, budget, and a 5,000-panel display limit. It stops when the annual target is reached and shows any unmet energy requirement. It is a greedy screening layout, not a guaranteed optimal design.

**Blue rectangles are proposed panels; green outlines are candidate roof regions.** Scroll or pinch to zoom, drag to pan, double-click to zoom in, or use the + / − / Reset controls. Use **Update budget / target** to replan a completed or saved analysis without new Google API calls. Proposal inputs, panel geometries in preview pixel coordinates, cost, and estimated energy are included in the JSON export; CSV and roof GeoJSON remain the regional roof-potential exports.

Existing solar panels and obstructions are not detected or excluded. Placement uses the displayed roof polygon and roof-average solar yield, so it does not resolve individual panel shading, roof pitch, fire setbacks, ownership, or structural readiness. The circles and panels are conceptual screening overlays, not an installation design.

### Command-line pipeline

```sh
.venv/bin/python process_solar_layers.py --address "1600 Amphitheatre Parkway, Mountain View, CA 94043" --radius 100 --unit meters --output outputs/my-search
```

This produces the original four TIFF layers, circle-masked TIFFs, aerial and sunlight preview PNGs, `results.json`, `roofs.geojson`, and `roofs.csv`. Restart the dashboard to open this CLI analysis from the saved-analysis menu. The pipeline requests only imagery and annual flux; monthly/hourly layers are not used for ranking. URLs can expire, so download immediately after requesting them. Keys and authenticated URLs are not written to result files.

### Detection and scoring

Roof rows and map tooltips use street addresses from Google reverse geocoding at a point inside each roof polygon. Matches whose Google coordinate falls outside the roof are labeled **nearby / approximate**. Several roof sections can share an address; region IDs remain available to distinguish them. Unresolved addresses remain explicitly unavailable. New searches look up each roof automatically, making additional potentially billable Geocoding API calls. For old saved searches, click **Look up roof addresses** to populate labels without downloading the solar layers again. Demo addresses are synthetic; lookup failures preserve the roof analysis.

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
