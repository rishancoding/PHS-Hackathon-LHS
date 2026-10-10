"""Conceptual panel placements constrained by a user budget and annual energy target."""

import math
from shapely.geometry import box, mapping, shape

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
                   panel_model=None, space_limit_m2=None):
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
    budget_count = math.floor(inputs["budget_usd"] / panel_cost)
    limit = min(budget_count, 5000)
    panels, energy = [], 0.0
    search_limited = False
    available_area = sum(max(0, r["properties"]["area_m2"] * assumptions["usable_roof_fraction"]
                         * r["properties"]["flux_coverage_percent"] / 100)
                         for r in result["roofs"] if r["properties"].get("annual_yield_kwh_per_kw") and r.get("preview_geometry"))
    if space_limit_m2 is not None:
        limit = min(limit, math.floor(space_limit_m2 / panel_area))
    roofs = sorted(result["roofs"], key=lambda r: r["properties"].get("annual_yield_kwh_per_kw") or 0, reverse=True)
    counts = {}
    for roof in roofs:
        properties = roof["properties"]
        yield_value = properties.get("annual_yield_kwh_per_kw")
        if not yield_value or not roof.get("preview_geometry"):
            continue
        polygon = shape(roof["preview_geometry"])
        min_x, min_y, max_x, max_y = polygon.bounds
        roof_limit = math.floor(properties["area_m2"] * assumptions["usable_roof_fraction"]
                                * properties["flux_coverage_percent"] / 100 / panel_area)
        # Try both axis-aligned orientations; use the one fitting more modules.
        remaining = limit - len(panels)
        if remaining <= 0:
            break
        needed = max(1, math.ceil((inputs["energy_target_kwh_year"] - energy) / (panel_kw * yield_value)))
        candidate_limit = min(roof_limit, remaining, needed)
        layouts = []
        for width_m, height_m in sorted({(panel_width, panel_height), (panel_height, panel_width)}):
            width, height = width_m / resolution, height_m / resolution
            step_x, step_y = (width_m + .2) / resolution, (height_m + .2) / resolution
            rows = max(0, math.floor((max_y - min_y - height) / step_y) + 1)
            cols = max(0, math.floor((max_x - min_x - width) / step_x) + 1)
            layout = []
            # Bound work for very large/irregular polygons or tiny catalog modules.
            for index in range(min(rows * cols, 50000)):
                if len(layout) >= candidate_limit:
                    break
                row, col = divmod(index, cols)
                x, y = min_x + col * step_x, min_y + row * step_y
                panel = box(x, y, x + width, y + height)
                if polygon.covers(panel):
                    layout.append(panel)
            if rows * cols > 50000 and len(layout) < candidate_limit:
                search_limited = True
            layouts.append(layout)
        layout = max(layouts, key=len)
        count = len(layout)
        for panel in layout:
            panel_energy = panel_kw * yield_value
            panels.append({"roof_id": properties["id"], "preview_geometry": mapping(panel),
                           "model_id": panel_model["model_id"] if panel_model else None,
                           "estimated_energy_kwh_year": round(panel_energy, 2)})
            energy += panel_energy
        counts[properties["id"]] = count
        if len(panels) >= limit or energy >= inputs["energy_target_kwh_year"]:
            break
    target_met = energy + 1e-8 >= inputs["energy_target_kwh_year"]
    if target_met:
        status = "Estimated annual energy target met within budget."
    elif search_limited:
        status = "Layout search limit reached; target remains unmet in this screening layout."
    elif space_limit_m2 is not None and limit < budget_count and len(panels) >= limit:
        status = "Usable space limit reached; estimated annual energy target remains unmet."
    elif budget_count == 0:
        status = "Budget is below the estimated installed cost of one panel."
    elif len(panels) >= 5000 and budget_count > 5000:
        status = "Reached the 5,000-panel planning limit; energy target remains unmet."
    elif len(panels) >= budget_count:
        status = "Budget reached; estimated annual energy target remains unmet."
    else:
        status = "Available candidate roof space is insufficient for the energy target."
    return {
        **inputs, "installation_estimate": cost_estimate,
        "estimated_cost_range_usd": [round(len(panels)*panel_cost*.75,2), round(len(panels)*panel_cost*1.25,2)] if cost_estimate else None,
        "panel_count": len(panels), "estimated_cost_usd": round(len(panels) * panel_cost, 2),
        "estimated_energy_kwh_year": round(energy, 2), "energy_shortfall_kwh_year": round(max(0, inputs["energy_target_kwh_year"] - energy), 2),
        "target_met": target_met, "status": status, "panel_kw": panel_kw,
        "panel_width_m": panel_width, "panel_height_m": panel_height,
        "panel_model": panel_model, "space_limit_m2": space_limit_m2, "available_roof_area_m2": round(available_area, 2),
        "panel_area_used_m2": round(len(panels) * panel_area, 3), "layout_search_limited": search_limited, "counts_by_roof": counts, "panels": panels,
        "notes": "Concept layout using roof-average yield and an estimated installed price. Existing panels, obstructions, roof ownership, slope, and required setbacks are not detected or excluded. This is not an installation design or quote.",
    }
