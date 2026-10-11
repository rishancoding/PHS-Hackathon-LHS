"""Transparent geographic screening estimates, not state-specific installer prices."""

import re

BASELINE = 3.15
REGIONS = {
    "Northeast": ("CT ME MA NH RI VT NJ NY PA", 1.12),
    "Midwest": ("IN IL MI OH WI IA KS MN MO NE ND SD", 0.95),
    "South": ("DE DC FL GA MD NC SC VA WV AL KY MS TN AR LA OK TX", 0.95),
    "West": ("AZ CO ID MT NV NM UT WY CA OR WA", 1.08),
}


def estimate_installation_cost(result):
    request = result.get("request", {})
    state = request.get("state")
    if not state:
        match = re.search(r"\b([A-Z]{2})\s+\d{5}(?:-\d{4})?\b", request.get("address", ""))
        state = match.group(1) if match else None
    region, factor = "US baseline (state unavailable)", 1
    for name, (states, multiplier) in REGIONS.items():
        if state in states.split():
            region, factor = name, multiplier
            break
    rate = round(BASELINE * factor, 3)
    return {
        "state": state,
        "region": region,
        "installed_cost_per_watt": rate,
        "low_cost_per_watt": round(rate * 0.75, 3),
        "high_cost_per_watt": round(rate * 1.25, 3),
        "baseline_cost_per_watt": BASELINE,
        "regional_multiplier": factor,
        "source_url": "https://www.nrel.gov/docs/fy25osti/92257.pdf",
        "notes": "All-in equipment and installation screening estimate before incentives, excluding batteries. Based on the Q1 2024 DOE/NREL residential benchmark in 2023 dollars. Regional multipliers and the ±25% range are illustrative modeling assumptions, not measured state prices or current quotes. No inflation adjustment; commercial and multi-building costs may differ.",
    }
