"""Address labels, readable findings, and consistent downloadable reports."""

import csv
import json
from pathlib import Path
from shapely.geometry import Point, shape
from solar_address import GEOCODING_URL, InputError, get_json


def add_roof_addresses(result, api_key=None, progress=lambda message: None):
    cache = {}
    unavailable = False
    for index, roof in enumerate(result["roofs"], 1):
        props = roof["properties"]
        if result.get("demo"):
            props.update(address=f"{100 + index * 2} Demo Solar Lane (synthetic)", address_status="synthetic")
            continue
        if props.get("address_status") in ("reverse_geocoded", "nearby_address"):
            continue
        props.update(address="Address unavailable", address_status="unavailable")
        if not api_key or unavailable:
            continue
        progress(f"Looking up roof addresses ({index}/{len(result['roofs'])})…")
        polygon = shape(roof["geometry"])
        point = polygon.representative_point()
        latlng = f"{point.y:.6f},{point.x:.6f}"
        try:
            if latlng not in cache:
                cache[latlng] = get_json(GEOCODING_URL, {"latlng": latlng, "result_type": "street_address|premise", "key": api_key})
            data = cache[latlng]
            if data.get("status") not in ("OK", "ZERO_RESULTS"):
                unavailable = True
                props["address_status"] = "lookup_failed"
                continue
            candidates = [r for r in data.get("results", []) if r.get("formatted_address")]
            if not candidates:
                continue
            matches = []
            for candidate in candidates:
                location = candidate.get("geometry", {}).get("location", {})
                if "lng" in location and "lat" in location and polygon.covers(Point(location["lng"], location["lat"])):
                    matches.append(candidate)
            chosen = (matches or candidates)[0]
            props.update(address=chosen["formatted_address"],
                         address_status="reverse_geocoded" if matches else "nearby_address")
        except InputError:
            props["address_status"] = "lookup_failed"
            unavailable = True
    return result


def describe_results(result):
    count = len(result["roofs"])
    radius = result["request"]["radius_meters"]
    coverage = result["analysis"]["flux_coverage_percent"]
    prefix = "This synthetic demo" if result.get("demo") else "The search"
    findings = (f"{prefix} found {count} candidate roof regions within {radius:,.0f} meters of "
                f"{result['request']['address']}, with sunlight data covering {coverage:,.1f}% of the search area. ")
    ranked = [r for r in result["roofs"] if r["properties"].get("estimated_energy_kwh_year") is not None]
    if ranked:
        best = max(ranked, key=lambda r: r["properties"]["estimated_energy_kwh_year"])
        props = best["properties"]
        address = props.get("address", "Address unavailable")
        label = address if address != "Address unavailable" else f"candidate {props['id']} (address unavailable)"
        if props.get("address_status") == "nearby_address":
            label = f"the roof near {address} (address approximate)"
        findings += f"The highest-ranked region is {label}, with an estimated {props['estimated_energy_kwh_year']:,.0f} kWh/year of screening potential. "
    plan = result.get("proposal")
    if plan:
        findings += (f"Your proposed {plan['panel_count']}-panel layout is estimated to cost ${plan['estimated_cost_usd']:,.0f} "
                     f"and produce {plan['estimated_energy_kwh_year']:,.0f} kWh/year. ")
        findings += ("It meets the annual energy target under the current assumptions." if plan["target_met"] else
                     f"It leaves a shortfall of {plan['energy_shortfall_kwh_year']:,.0f} kWh/year against your target.")
    elif not ranked:
        findings += "No roof regions with usable sunlight data were available to estimate solar potential."
    next_steps = (
        "Review the roof outlines and address matches, then confirm ownership and existing panels or obstructions. "
        "Ask a qualified solar installer to verify usable roof area, condition, shading, setbacks, and electrical capacity, "
        "and obtain a site-specific layout and quote before making an installation decision."
    )
    if not count:
        next_steps = "Try another address or radius and check Google Solar coverage. " + next_steps
    elif plan and not plan["target_met"]:
        next_steps = "Compare a larger budget, a lower energy target, or additional eligible roofs. " + next_steps
    if result.get("demo"):
        next_steps = "Run a real address search before acting on these synthetic results. " + next_steps
    result["report"] = {"findings": findings.strip(), "next_steps": next_steps}
    return result


def save_report(result, directory):
    describe_results(result)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "results.json").write_text(json.dumps(result, indent=2, allow_nan=False))
    (directory / "summary.txt").write_text(result["report"]["findings"] + "\n\nNext steps\n" + result["report"]["next_steps"] + "\n")
    features = [{k: v for k, v in roof.items() if k != "preview_geometry"} for roof in result["roofs"]]
    (directory / "roofs.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}, allow_nan=False))
    columns = ["rank", "id", "address", "address_status", "area_m2", "area_ft2", "annual_yield_kwh_per_kw",
               "estimated_capacity_kw", "estimated_energy_kwh_year", "flux_coverage_percent", "partial_roof",
               "latitude", "longitude", "mean_elevation_m"]
    with (directory / "roofs.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows({k: roof["properties"].get(k) for k in columns} for roof in result["roofs"])
