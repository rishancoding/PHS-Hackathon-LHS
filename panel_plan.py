"""Conceptual panel placements constrained by a user budget and annual energy target."""

import math

import numpy as np
from shapely import box as vector_boxes, covers, prepare
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

from solar_address import InputError
from installation_cost import estimate_installation_cost


def validate_plan_inputs(budget_usd=25000, energy_target_kwh_year=10000, installed_cost_per_watt=3):
    values = {}
    for field, value in (("budget_usd", budget_usd), ("energy_target_kwh_year", energy_target_kwh_year),
                         ("installed_cost_per_watt", installed_cost_per_watt)):
        try:
            value = float(value)
        except (ValueError, TypeError):
            raise InputError("Budget, annual energy target, and cost per watt must be numbers.") from None
        if not math.isfinite(value) or value <= 0:
            raise InputError("Budget, annual energy target, and cost per watt must be finite and greater than zero.")
        values[field] = value
    return values


def propose_panels(result, budget_usd=25000, energy_target_kwh_year=10000, installed_cost_per_watt=None,
                   panel_model=None, space_limit_m2=None, _geometry_cache=None, budget_includes_installation=True):
    cost_estimate = estimate_installation_cost(result) if installed_cost_per_watt is None else None
    if cost_estimate:
        installed_cost_per_watt = cost_estimate["installed_cost_per_watt"]
    inputs = validate_plan_inputs(budget_usd, energy_target_kwh_year, installed_cost_per_watt)
    if space_limit_m2 is not None:
        try:
            space_limit_m2 = float(space_limit_m2)
        except (ValueError, TypeError):
            raise InputError("Space limit must be a positive number in square meters.") from None
        if not math.isfinite(space_limit_m2) or space_limit_m2 <= 0:
            raise InputError("Space limit must be finite and greater than zero.")
    resolution = result["analysis"]["resolution_m"]
    assumptions = result["assumptions"]
    # Catalog dimensions/watts when supplied; retain the generic layout for standalone callers.
    panel_width = panel_model["width_m"] if panel_model else 1
    panel_height = panel_model["height_m"] if panel_model else 2
    panel_area = panel_width * panel_height
    panel_kw = panel_model["rated_power_w"] / 1000 if panel_model else 2 * assumptions["panel_kw_per_m2"]
    panel_cost = panel_kw * 1000 * inputs["installed_cost_per_watt"]
    if not isinstance(budget_includes_installation, bool):
        raise InputError("Budget includes installation must be true or false.")
    if not budget_includes_installation and not panel_model:
        raise InputError("A catalog panel model is required for a panels-only budget.")
    budget_panel_cost = panel_cost if budget_includes_installation else panel_model["price_usd"]
    if not math.isfinite(budget_panel_cost) or budget_panel_cost <= 0:
        raise InputError("Panel budget cost must be finite and greater than zero.")
    budget_count = math.floor(inputs["budget_usd"] / budget_panel_cost)
    limit = min(budget_count, 5000)
    panels, energy = [], 0.0
    search_limited = False
    if space_limit_m2 is not None:
        limit = min(limit, math.floor(space_limit_m2 / panel_area))
    geometry_cache = {} if _geometry_cache is None else _geometry_cache
    blocked = {}
    for feature in result.get("existing_panels", []):
        blocked.setdefault(feature["properties"]["roof_id"], []).append(
            shape(feature["preview_geometry"]).buffer(.2 / resolution, join_style="mitre"))
    zones = [box(*zone["bounds"]) for zone in result.get("blocked_zones", [])]

    def usable_geometry(roof):
        """Roof outline minus possible existing panels and user no-panel zones, plus the share kept."""
        key = id(roof)
        if key not in geometry_cache:
            original = shape(roof["preview_geometry"])
            removed = blocked.get(roof["properties"]["id"], []) + [zone for zone in zones if zone.intersects(original)]
            polygon = original.difference(unary_union(removed)) if removed else original
            prepare(polygon)
            geometry_cache[key] = (polygon, polygon.area / original.area if original.area else 0)
        return geometry_cache[key]

    available_area = sum(max(0, r["properties"]["area_m2"] * usable_geometry(r)[1] * assumptions["usable_roof_fraction"]
                             * r["properties"]["flux_coverage_percent"] / 100)
                         for r in result["roofs"] if r["properties"].get("annual_yield_kwh_per_kw") and r.get("preview_geometry"))
    roofs = sorted(result["roofs"], key=lambda r: r["properties"].get("annual_yield_kwh_per_kw") or 0, reverse=True)
    counts = {}
    for roof in roofs:
        properties = roof["properties"]
        yield_value = properties.get("annual_yield_kwh_per_kw")
        if not yield_value or not roof.get("preview_geometry"):
            continue
        polygon, kept = usable_geometry(roof)
        roof_limit = math.floor(properties["area_m2"] * kept * assumptions["usable_roof_fraction"]
                                * properties["flux_coverage_percent"] / 100 / panel_area)
        # Try both axis-aligned orientations; use the one fitting more modules.
        remaining = limit - len(panels)
        if remaining <= 0:
            break
        needed = max(1, math.ceil((inputs["energy_target_kwh_year"] - energy) / (panel_kw * yield_value)))
        candidate_limit = min(roof_limit, remaining, needed)
        layouts = []
        for width_m, height_m in sorted({(panel_width, panel_height), (panel_height, panel_width)}):
            layout, limited = fit_grid(polygon, width_m, height_m, resolution, candidate_limit)
            search_limited |= limited
            layouts.append(layout)
        layout = max(layouts, key=len)
        count = len(layout)
        for panel in layout:
            panel_energy = panel_kw * yield_value
            left, top, right, bottom = panel.bounds
            panels.append({"roof_id": properties["id"], "preview_geometry": mapping(panel),
                           "width_m": (right - left) * resolution, "height_m": (bottom - top) * resolution,
                           "model_id": panel_model["model_id"] if panel_model else None,
                           "estimated_energy_kwh_year": round(panel_energy, 2)})
            energy += panel_energy
        counts[properties["id"]] = count
        if len(panels) >= limit or energy >= inputs["energy_target_kwh_year"]:
            break
    target_met = energy + 1e-8 >= inputs["energy_target_kwh_year"]
    if target_met:
        status = "Estimated annual energy target met within the selected budget scope."
    elif search_limited:
        status = "Layout search limit reached; target remains unmet in this screening layout."
    elif space_limit_m2 is not None and limit < budget_count and len(panels) >= limit:
        status = "Usable space limit reached; estimated annual energy target remains unmet."
    elif budget_count == 0:
        status = ("Budget is below the estimated installed cost of one panel." if budget_includes_installation
                  else "Budget is below the panel hardware cost of one panel.")
    elif len(panels) >= 5000 and budget_count > 5000:
        status = "Reached the 5,000-panel planning limit; energy target remains unmet."
    elif len(panels) >= budget_count:
        status = "Budget reached; estimated annual energy target remains unmet."
    else:
        status = "Available candidate roof space is insufficient for the energy target."
    return {
        **inputs, "installation_estimate": cost_estimate,
        "budget_includes_installation": budget_includes_installation,
        "budget_cost_usd": round(len(panels) * budget_panel_cost, 2),
        "estimated_cost_range_usd": [round(len(panels)*panel_cost*.75,2), round(len(panels)*panel_cost*1.25,2)] if cost_estimate else None,
        "panel_count": len(panels), "estimated_cost_usd": round(len(panels) * panel_cost, 2),
        "estimated_energy_kwh_year": round(energy, 2), "energy_shortfall_kwh_year": round(max(0, inputs["energy_target_kwh_year"] - energy), 2),
        "target_met": target_met, "status": status, "panel_kw": panel_kw,
        "panel_width_m": panel_width, "panel_height_m": panel_height,
        "panel_model": panel_model, "space_limit_m2": space_limit_m2, "available_roof_area_m2": round(available_area, 2),
        "panel_area_used_m2": round(len(panels) * panel_area, 3), "layout_search_limited": search_limited, "counts_by_roof": counts, "panels": panels,
        "notes": "Concept layout using roof-average yield and an estimated installed price. New panels avoid areas flagged as possible existing panels (a color-based check) and any no-panel zones you drew. Other obstructions, roof ownership, slope, and required setbacks are not detected or excluded. This is not an installation design or quote.",
    }


def fit_grid(polygon, width_m, height_m, resolution, candidate_limit):
    """Same grid and scan order as before, with batched GEOS checks instead of Python loops."""
    if candidate_limit <= 0:
        return [], False
    min_x, min_y, max_x, max_y = polygon.bounds
    width, height = width_m / resolution, height_m / resolution
    step_x, step_y = (width_m + .2) / resolution, (height_m + .2) / resolution
    rows = max(0, math.floor((max_y - min_y - height) / step_y) + 1)
    cols = max(0, math.floor((max_x - min_x - width) / step_x) + 1)
    layout = []
    positions = min(rows * cols, 50000)
    batch_size = min(1024, max(64, candidate_limit * 2))
    for start in range(0, positions, batch_size):
        indices = np.arange(start, min(start + batch_size, positions))
        row, col = np.divmod(indices, cols)
        x, y = min_x + col * step_x, min_y + row * step_y
        rectangles = vector_boxes(x, y, x + width, y + height)
        fitting = rectangles[covers(polygon, rectangles)]
        layout.extend(fitting[:candidate_limit - len(layout)].tolist())
        if len(layout) >= candidate_limit:
            break
    return layout, rows * cols > 50000 and len(layout) < candidate_limit
