"""Download Google layers, analyze a metric circle, and rank candidate roof regions."""

import argparse
import getpass
import json
import math
import os
import shutil
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import urlopen

import numpy as np
from PIL import Image
from pyproj import CRS, Transformer
import rasterio
from rasterio.features import shapes
from rasterio.transform import from_origin
from rasterio.warp import reproject, Resampling
from scipy import ndimage
from shapely.geometry import Point, mapping, shape
from shapely import transform as shapely_transform

from existing_panels import DETECTOR as EXISTING_PANEL_DETECTOR, detect_existing_panels
from panel_plan import DEFAULT_SYSTEM_DERATE
from solar_address import DEFAULT_API_KEY, InputError, SOLAR_URL, get_json, https_context, prepare_request
from roof_details import save_report

LAYERS = {"rgb": "rgbUrl", "mask": "maskUrl", "annual_flux": "annualFluxUrl", "dsm": "dsmUrl"}


def transform_geometry(function, geometry):
    return shapely_transform(geometry, function, interleaved=False)


def download_layers(data, api_key, directory, progress=lambda message: None):
    """Fetch only Google's authenticated TIFF URLs; never save keyed URLs to disk."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    requests = []
    for name, field in LAYERS.items():
        url = data.get(field)
        if not url:
            raise InputError(f"Google did not return the required {name} layer.")
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != "solar.googleapis.com":
            raise InputError("Unexpected layer host. Only Google Solar HTTPS downloads are allowed.")
        query = [(k, v) for k, v in parse_qsl(parts.query) if k != "key"]
        query.append(("key", api_key))
        authenticated = urlunsplit(parts._replace(query=urlencode(query)))
        requests.append((name, authenticated))

    def download(item):
        name, authenticated = item
        target = directory / f"{name}.tif"
        temporary = target.with_suffix(".part")

        try:
            with (
                urlopen(authenticated, timeout=90, context=https_context()) as response,
                temporary.open("wb") as output,
            ):
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > 512 * 1024 * 1024:
                        raise InputError("A layer exceeds the 512 MB download limit. Use a smaller radius.")
                    output.write(chunk)
            with rasterio.open(temporary) as dataset:
                if dataset.crs is None:
                    raise InputError(f"The {name} raster has no coordinate reference system.")
            temporary.replace(target)
        except HTTPError as exc:
            raise InputError(
                f"{name} download failed (HTTP {exc.code}). Check Solar permissions; layer URLs expire."
            ) from None
        except (URLError, OSError) as exc:
            raise InputError(
                f"Could not download or read {name}. Check your network and request fresh layers."
            ) from None
        finally:
            if temporary.exists():
                temporary.unlink()
        return name, target

    paths = {}
    progress("Downloading imagery, roof mask, sunlight, and elevation together…")
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(download, item) for item in requests]
        for future in as_completed(futures):
            name, target = future.result()
            paths[name] = target
    return paths


def aligned_band(path, band, grid, categorical=False):
    """Reproject each source into the same meter-based grid, preserving missing data."""
    destination = np.full((grid["height"], grid["width"]), np.nan, dtype=np.float32)
    with rasterio.open(path) as source:
        if source.crs is None or band > source.count:
            raise InputError(f"Invalid raster metadata or missing band in {Path(path).name}.")
        reproject(
            source=rasterio.band(source, band),
            destination=destination,
            src_transform=source.transform,
            src_crs=source.crs,
            src_nodata=source.nodata
            if source.nodata is not None
            else (-9999 if not categorical and np.issubdtype(np.dtype(source.dtypes[band - 1]), np.floating) else None),
            dst_transform=grid["transform"],
            dst_crs=grid["crs"],
            dst_nodata=np.nan,
            resampling=Resampling.nearest if categorical else Resampling.bilinear,
        )
    return destination


def metric_grid(latitude, longitude, radius, pixel_size):
    # Local UTM distances and areas avoid the large distortions of Web Mercator.
    zone = min(60, max(1, int((longitude + 180) // 6) + 1))
    crs = CRS.from_epsg((32600 if latitude >= 0 else 32700) + zone)
    project = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    x, y = project.transform(longitude, latitude)
    size = max(pixel_size, 2 * radius / 2000)
    width = max(2, math.ceil(2 * radius / size))
    return {
        "crs": crs,
        "transform": from_origin(x - radius, y + radius, size, size),
        "width": width,
        "height": width,
        "resolution": size,
        "center_x": x,
        "center_y": y,
    }


def process_layers(
    paths,
    request,
    directory,
    usable_fraction=0.65,
    panel_kw_per_m2=0.2,
    min_area_m2=10,
    progress=lambda message: None,
    demo=False,
    system_derate=DEFAULT_SYSTEM_DERATE,
):
    if not 0 < usable_fraction <= 1 or not 0 < panel_kw_per_m2 <= 1:
        raise InputError("Usable fraction and panel density must be greater than 0 and at most 1.")
    if not math.isfinite(min_area_m2) or min_area_m2 <= 0:
        raise InputError("Minimum roof area must be finite and greater than 0.")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    radius = request["radius_meters"]
    grid = metric_grid(
        request["latitude"], request["longitude"], radius, request["solar_parameters"]["pixelSizeMeters"]
    )
    progress("Aligning imagery, roof mask, sunlight, and elevation…")
    roof_mask = aligned_band(paths["mask"], 1, grid, categorical=True)
    annual = aligned_band(paths["annual_flux"], 1, grid)
    dsm = aligned_band(paths["dsm"], 1, grid)
    rgb = np.stack([aligned_band(paths["rgb"], band, grid) for band in (1, 2, 3)])
    valid_flux = np.isfinite(annual) & (annual >= 0)
    row, col = np.ogrid[: grid["height"], : grid["width"]]
    pixel_x = grid["transform"].c + (col + 0.5) * grid["resolution"]
    pixel_y = grid["transform"].f - (row + 0.5) * grid["resolution"]
    circle_pixels = ((pixel_x - grid["center_x"]) ** 2 + (pixel_y - grid["center_y"]) ** 2) <= radius**2
    roof_pixels = np.isfinite(roof_mask) & (roof_mask > 0)
    # Label before clipping, so one roof cut by the circle remains a single candidate.
    labels, count = ndimage.label(roof_pixels)
    clipped = np.where(circle_pixels, labels, 0).astype(np.int32)
    pixel_area = grid["resolution"] ** 2
    sizes = np.bincount(clipped.ravel(), minlength=count + 1)
    keep = np.flatnonzero(sizes * pixel_area >= min_area_m2)
    keep = keep[keep != 0]
    filtered = np.where(np.isin(clipped, keep), clipped, 0).astype(np.int32)
    circle = Point(grid["center_x"], grid["center_y"]).buffer(radius, quad_segs=128)
    to_wgs84 = Transformer.from_crs(grid["crs"], "EPSG:4326", always_xy=True).transform
    progress("Detecting candidate roofs and calculating solar estimates…")
    candidates = {}
    for geometry, value in shapes(filtered, mask=filtered > 0, transform=grid["transform"]):
        label = int(value)
        polygon = shape(geometry).intersection(circle)
        if polygon.is_empty:
            continue
        candidates[label] = polygon if label not in candidates else candidates[label].union(polygon)
    # Group pixel statistics in one pass instead of scanning the image once per roof.
    values = filtered.ravel()
    flux_counts = np.bincount(values, weights=valid_flux.ravel(), minlength=count + 1)
    flux_sums = np.bincount(values, weights=np.where(valid_flux, annual, 0).ravel(), minlength=count + 1)
    dsm_valid = np.isfinite(dsm) & (dsm != -9999)
    dsm_counts = np.bincount(values, weights=dsm_valid.ravel(), minlength=count + 1)
    dsm_sums = np.bincount(values, weights=np.where(dsm_valid, dsm, 0).ravel(), minlength=count + 1)
    touches_circle = ndimage.binary_dilation(~circle_pixels) & circle_pixels
    touches_edge = np.zeros_like(circle_pixels)
    touches_edge[[0, -1], :] = True
    touches_edge[:, [0, -1]] = True
    boundary_ids = set(np.unique(filtered[touches_circle | touches_edge]))
    roofs = []
    for label, polygon in candidates.items():
        if polygon.area < min_area_m2:
            continue
        samples = flux_counts[label]
        mean_flux = float(flux_sums[label] / samples) if samples else None
        coverage = float(samples / sizes[label])
        # Use only the area with valid sunlight; do not extrapolate missing flux.
        analyzed_area = polygon.area * coverage
        capacity = analyzed_area * usable_fraction * panel_kw_per_m2
        energy = capacity * mean_flux * system_derate if mean_flux is not None else None
        centroid = transform_geometry(to_wgs84, polygon.centroid)
        props = {
            "id": f"roof-{label}",
            "rank": None,
            "area_m2": round(polygon.area, 2),
            "area_ft2": round(polygon.area * 10.7639104, 2),
            "flux_coverage_percent": round(coverage * 100, 1),
            "annual_yield_kwh_per_kw": round(mean_flux, 1) if mean_flux is not None else None,
            "estimated_capacity_kw": round(capacity, 2) if samples else None,
            "estimated_energy_kwh_year": round(energy, 1) if energy is not None else None,
            "mean_elevation_m": round(float(dsm_sums[label] / dsm_counts[label]), 2) if dsm_counts[label] else None,
            "partial_roof": label in boundary_ids,
            "latitude": centroid.y,
            "longitude": centroid.x,
        }
        # Pixel coordinates map exactly to the preview image, including polygon holes.
        inverse = ~grid["transform"]

        def to_pixel(x, y, z=None):
            return inverse.a * np.asarray(x) + inverse.b * np.asarray(y) + inverse.c, inverse.d * np.asarray(
                x
            ) + inverse.e * np.asarray(y) + inverse.f

        roofs.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": mapping(transform_geometry(to_wgs84, polygon)),
                "preview_geometry": mapping(transform_geometry(to_pixel, polygon.simplify(grid["resolution"]))),
            }
        )
    roofs.sort(
        key=lambda feature: feature["properties"]["estimated_energy_kwh_year"]
        if feature["properties"]["estimated_energy_kwh_year"] is not None
        else -1,
        reverse=True,
    )
    for rank, roof in enumerate(roofs, 1):
        roof["properties"]["rank"] = rank
    progress("Checking roofs for possible existing panels…")
    t = grid["transform"]

    def to_world(x, y, z=None):
        return t.a * np.asarray(x) + t.b * np.asarray(y) + t.c, t.d * np.asarray(x) + t.e * np.asarray(y) + t.f

    existing_area = {roof["properties"]["id"]: 0.0 for roof in roofs}
    existing_panels = []
    for polygon, label, area in detect_existing_panels(rgb, filtered, grid["resolution"]):
        roof_id = f"roof-{label}"
        if roof_id not in existing_area:
            continue
        existing_area[roof_id] += area
        existing_panels.append(
            {
                "type": "Feature",
                "properties": {"roof_id": roof_id, "area_m2": round(area, 1)},
                "geometry": mapping(transform_geometry(to_wgs84, transform_geometry(to_world, polygon))),
                "preview_geometry": mapping(polygon.simplify(1)),
            }
        )
    for roof in roofs:
        roof["properties"]["existing_panel_area_m2"] = round(existing_area[roof["properties"]["id"]], 1)
    image = np.clip(np.nan_to_num(np.moveaxis(rgb, 0, -1)), 0, 255).astype(np.uint8)
    alpha = (circle_pixels & np.all(np.isfinite(rgb), axis=0)).astype(np.uint8) * 255
    # Compact previews: the map frame is circular, so the photo needs no alpha (JPEG, ~10x smaller).
    Image.fromarray(np.where(alpha[..., None] > 0, image, 226).astype(np.uint8)).save(
        directory / "aerial.jpg", quality=85, optimize=True
    )
    # A separate annual-yield overlay makes differences in sunlight visible.
    sunlight = valid_flux & circle_pixels
    if sunlight.any():
        low, high = np.percentile(annual[sunlight], [5, 95])
        normalized = np.clip((np.nan_to_num(annual) - low) / max(float(high - low), 1), 0, 1)
    else:
        normalized = np.zeros_like(annual)
    heat = np.stack([255 * normalized, 90 + 140 * normalized, 160 * (1 - normalized), sunlight * 180], axis=-1).astype(
        np.uint8
    )
    Image.fromarray(heat).save(directory / "sunlight.webp", quality=80, method=4)
    for name, data in (
        ("roof_mask", np.where(circle_pixels, roof_mask, np.nan)),
        ("annual_flux", np.where(circle_pixels, annual, np.nan)),
        ("dsm", np.where(circle_pixels, dsm, np.nan)),
    ):
        with rasterio.open(
            directory / f"clipped_{name}.tif",
            "w",
            driver="GTiff",
            height=grid["height"],
            width=grid["width"],
            count=1,
            dtype="float32",
            crs=grid["crs"],
            transform=grid["transform"],
            nodata=-9999,
            compress="deflate",
        ) as output:
            output.write(np.where(np.isfinite(data), data, -9999).astype(np.float32), 1)
    result = {
        "request": request,
        "demo": demo,
        "detector": "Google rooftop mask + connected components",
        "assumptions": {
            "usable_roof_fraction": usable_fraction,
            "panel_kw_per_m2": panel_kw_per_m2,
            "minimum_roof_area_m2": min_area_m2,
            "system_derate": system_derate,
        },
        "analysis": {
            "resolution_m": grid["resolution"],
            "crs": grid["crs"].to_string(),
            "raster_width": grid["width"],
            "raster_height": grid["height"],
            "flux_coverage_percent": round(float(sunlight.sum() / circle_pixels.sum() * 100), 1),
        },
        "summary": {
            "candidate_roofs": len(roofs),
            "estimated_energy_kwh_year": round(
                sum(r["properties"]["estimated_energy_kwh_year"] or 0 for r in roofs), 1
            ),
        },
        "roofs": roofs,
        "existing_panels": existing_panels,
        "existing_panel_detector": EXISTING_PANEL_DETECTOR,
        "preview_files": {"aerial": "aerial.jpg", "sunlight": "sunlight.webp"},
        "notes": [
            "Candidate roof regions are not verified building identities; touching roofs may merge.",
            "Area is horizontal footprint within the radius, not sloped roof surface area.",
            "Energy = sunlight-covered area × usable fraction × panel kW/m² × annual yield (kWh/kW/year) × system efficiency.",
            "Missing sunlight is excluded. Roof portions beyond the radius are excluded and flagged.",
            "Screening estimates do not establish structural condition, setbacks, or installation readiness.",
            "Possible existing panels are flagged by color, not a trained model; verify them on the aerial image.",
        ],
    }
    # Street addresses are a separate step run after the analysis (Look up roof addresses).
    for roof in roofs:
        roof["properties"].update(address="Address not looked up", address_status="not_looked_up")
    save_report(result, directory)
    return result


def find_saved_layers(output_root, request, exclude=None):
    """Most recent saved run with the same center, radius and pixel size, if its four layers exist."""
    output_root = Path(output_root)
    if not output_root.is_dir():
        return None
    key = (
        round(request["latitude"], 6),
        round(request["longitude"], 6),
        round(request["radius_meters"], 3),
        request["solar_parameters"]["pixelSizeMeters"],
    )
    matches = []
    for results_file in output_root.glob("*/results.json"):
        folder = results_file.parent
        if folder == exclude or not all((folder / "layers" / f"{name}.tif").is_file() for name in LAYERS):
            continue
        try:
            saved = json.loads(results_file.read_text())
            saved_request = saved["request"]
            saved_key = (
                round(saved_request["latitude"], 6),
                round(saved_request["longitude"], 6),
                round(saved_request["radius_meters"], 3),
                saved_request["solar_parameters"]["pixelSizeMeters"],
            )
        except (ValueError, KeyError, TypeError, OSError):
            continue
        if saved_key == key and not saved.get("demo"):
            matches.append((results_file.stat().st_mtime, folder, saved.get("imagery") or {}))
    return max(matches, key=lambda match: match[0]) if matches else None


def reuse_layers(source, directory):
    """Give the new run its own copy of saved layers (hard links when possible)."""
    target = Path(directory) / "layers"
    target.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name in LAYERS:
        source_file, target_file = Path(source) / "layers" / f"{name}.tif", target / f"{name}.tif"
        if not target_file.exists():
            try:
                os.link(source_file, target_file)
            except OSError:
                shutil.copy2(source_file, target_file)
        paths[name] = target_file
    return paths


def request_layers(request, api_key):
    """Ask for HIGH-quality layers, falling back once to MEDIUM where Google has no HIGH coverage."""
    parameters = {**request["solar_parameters"], "key": api_key}
    try:
        return get_json(SOLAR_URL, parameters)
    except InputError as exc:
        if "HTTP 404" not in str(exc):
            raise
    request["solar_parameters"]["requiredQuality"] = "MEDIUM"
    return get_json(SOLAR_URL, {**request["solar_parameters"], "key": api_key})


def run_pipeline(
    address,
    radius,
    unit,
    api_key,
    directory,
    progress=lambda message: None,
    reuse_imagery=True,
    saved_root=None,
    **options,
):
    if not api_key:
        raise InputError("Set GOOGLE_MAPS_API_KEY or supply a key in the dashboard.")
    progress("Finding the address coordinates…")
    request = prepare_request(address, radius, unit, api_key)
    # Only the layers used by this pipeline, avoiding unused monthly/hourly downloads.
    request["solar_parameters"]["view"] = "IMAGERY_AND_ANNUAL_FLUX_LAYERS"
    directory = Path(directory)
    saved = find_saved_layers(saved_root or directory.parent, request, exclude=directory) if reuse_imagery else None
    if saved:
        saved_time, folder, imagery = saved
        progress("Using saved imagery for this address…")
        paths = reuse_layers(folder, directory)
        imagery = {**imagery, "reused_from": time.strftime("%Y-%m-%d %H:%M", time.localtime(saved_time))}
        request["solar_parameters"]["requiredQuality"] = (
            imagery.get("imageryQuality") or request["solar_parameters"]["requiredQuality"]
        )
    else:
        progress("Requesting regional solar data…")
        data = request_layers(request, api_key)
        paths = download_layers(data, api_key, directory / "layers", progress)
        imagery = {key: data.get(key) for key in ("imageryDate", "imageryProcessedDate", "imageryQuality")}
    result = process_layers(paths, request, directory, progress=progress, **options)
    result["imagery"] = imagery
    save_report(result, directory)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True)
    parser.add_argument("--radius", required=True, type=float)
    parser.add_argument("--unit", choices=("feet", "meters"), default="meters")
    parser.add_argument("--output", default="outputs/latest")
    args = parser.parse_args()
    key = (
        os.environ.get("GOOGLE_MAPS_API_KEY", DEFAULT_API_KEY).strip()
        or getpass.getpass("Google API key (hidden): ").strip()
    )
    try:
        result = run_pipeline(
            args.address, args.radius, args.unit, key, args.output, progress=lambda message: print(message, flush=True)
        )
        print(f"Found {result['summary']['candidate_roofs']} candidate roofs. Results: {Path(args.output).resolve()}")
        return 0
    except (InputError, rasterio.errors.RasterioError) as exc:
        print(str(exc).replace(key, "[redacted]") if key else str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
