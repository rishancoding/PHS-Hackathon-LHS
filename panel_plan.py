"""Conceptual panel placements constrained by a user budget and annual energy target."""

import math
from shapely.geometry import box, mapping, shape

from solar_address import InputError


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


def propose_panels(result, budget_usd=25000, energy_target_kwh_year=10000, installed_cost_per_watt=3):
    inputs = validate_plan_inputs(budget_usd, energy_target_kwh_year, installed_cost_per_watt)
    resolution = result["analysis"]["resolution_m"]
    assumptions = result["assumptions"]
    # Illustrative 1 × 2 meter modules, with power from the user's panel-density assumption.
    panel_kw = 2 * assumptions["panel_kw_per_m2"]
    panel_cost = panel_kw * 1000 * inputs["installed_cost_per_watt"]
    budget_count = math.floor(inputs["budget_usd"] / panel_cost)
    limit = min(budget_count, 5000)
    panels, energy = [], 0.0
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
                                * properties["flux_coverage_percent"] / 100 / 2)
        count = 0
        width, height = 1 / resolution, 2 / resolution
        step_x, step_y = 1.2 / resolution, 2.2 / resolution
        rows = max(0, math.floor((max_y - min_y - height) / step_y) + 1)
        cols = max(0, math.floor((max_x - min_x - width) / step_x) + 1)
        for row in range(rows):
            if count >= roof_limit or len(panels) >= limit or energy >= inputs["energy_target_kwh_year"]:
                break
            for col in range(cols):
                if count >= roof_limit or len(panels) >= limit or energy >= inputs["energy_target_kwh_year"]:
                    break
                x, y = min_x + col * step_x, min_y + row * step_y
                panel = box(x, y, x + width, y + height)
                if not polygon.covers(panel):
                    continue
                panel_energy = panel_kw * yield_value
                panels.append({"roof_id": properties["id"], "preview_geometry": mapping(panel),
                               "estimated_energy_kwh_year": round(panel_energy, 2)})
                energy += panel_energy
                count += 1
        counts[properties["id"]] = count
        if len(panels) >= limit or energy >= inputs["energy_target_kwh_year"]:
            break
    target_met = energy >= inputs["energy_target_kwh_year"]
    if target_met:
        status = "Estimated annual energy target met within budget."
    elif budget_count == 0:
        status = "Budget is below the assumed installed cost of one panel."
    elif len(panels) >= 5000 and budget_count > 5000:
        status = "Reached the 5,000-panel planning limit; energy target remains unmet."
    elif len(panels) >= budget_count:
        status = "Budget reached; estimated annual energy target remains unmet."
    else:
        status = "Available candidate roof space is insufficient for the energy target."
    return {
        **inputs, "panel_count": len(panels), "estimated_cost_usd": round(len(panels) * panel_cost, 2),
        "estimated_energy_kwh_year": round(energy, 2), "energy_shortfall_kwh_year": round(max(0, inputs["energy_target_kwh_year"] - energy), 2),
        "target_met": target_met, "status": status, "panel_kw": panel_kw,
        "panel_width_m": 1, "panel_height_m": 2, "counts_by_roof": counts, "panels": panels,
        "notes": "Concept layout using roof-average yield and an assumed installed price. Existing panels, obstructions, roof ownership, slope, and required setbacks are not detected or excluded. This is not an installation design or quote.",
    }
