"""Screening estimates of bill savings, payback and emissions for a proposed layout.

Every constant is sourced below. These are planning figures, not a utility-bill or
tax analysis: they assume each kWh produced offsets one kWh bought at the state's
average residential price, and include no incentives.
"""

# EIA Open Data API, electricity/retail-sales, residential sector, 2025 annual average price (cents/kWh).
# Preliminary 2025 values. https://api.eia.gov/v2/electricity/retail-sales/ (query: sectorid=RES, period=2025)
RESIDENTIAL_PRICE_CENTS = {
    "AL": 16.1, "AR": 12.84, "AZ": 15.32, "CA": 32.54, "CO": 15.85, "CT": 29.38, "DC": 21.94, "DE": 17.13,
    "FL": 15.24, "GA": 14.73, "IA": 13.72, "ID": 11.82, "IL": 17.69, "IN": 16.23, "KS": 14.56, "KY": 13.24,
    "LA": 12.57, "MA": 30.48, "MD": 19.48, "ME": 27.78, "MI": 20.01, "MN": 15.82, "MO": 13.49, "MS": 14.03,
    "MT": 12.98, "NC": 14.02, "ND": 11.81, "NE": 12.34, "NH": 24.56, "NJ": 22.63, "NM": 15.08, "NV": 13.15,
    "NY": 26.39, "OH": 16.96, "OK": 13.12, "OR": 15.37, "PA": 19.3, "RI": 29.46, "SC": 14.96, "SD": 13.38,
    "TN": 13.18, "TX": 15.47, "UT": 13.07, "VA": 15.28, "VT": 22.92, "WA": 13.11, "WI": 18.16, "WV": 15.41,
    "WY": 13.38,
}
US_AVERAGE_PRICE_CENTS = 17.3

# EPA eGRID2023 (rev. 2, June 2025), Table 1: U.S. non-baseload CO2 output rate, lb/MWh.
# Non-baseload generation is what rooftop solar mostly displaces. https://www.epa.gov/egrid/summary-data
NON_BASELOAD_LB_CO2_PER_MWH = 1372.477
KG_PER_LB = 0.45359237

# EIA retail sales / customers, residential, 2025: about 10,424 kWh per customer per year
# (EIA reports 865 kWh/month for 2024). https://www.eia.gov/todayinenergy/detail.php?id=65244
AVERAGE_HOME_KWH_PER_YEAR = 10424

SOURCES = {
    "electricity_price": "EIA retail sales, residential average price, 2025 (preliminary)",
    "emissions": "EPA eGRID2023, U.S. non-baseload CO2 rate (1,372 lb/MWh)",
    "average_home": "EIA, average residential use (about 10,400 kWh/year)",
    "incentives": "Federal residential clean energy credit (25D) ended for systems placed in service after 2025 (IRS)",
}


def estimate_impact(plan, state=None):
    """Yearly savings, simple payback, CO2 avoided and homes' worth of electricity for a plan."""
    energy = (plan or {}).get("estimated_energy_kwh_year") or 0
    cost = (plan or {}).get("estimated_cost_usd") or 0
    price = RESIDENTIAL_PRICE_CENTS.get(state, US_AVERAGE_PRICE_CENTS)
    savings = energy * price / 100
    return {
        "electricity_price_cents_per_kwh": price,
        "price_region": state if state in RESIDENTIAL_PRICE_CENTS else "US average",
        "annual_bill_savings_usd": round(savings, 2),
        "simple_payback_years": round(cost / savings, 1) if savings > 0 and cost > 0 else None,
        "co2_avoided_tonnes_per_year": round(energy / 1000 * NON_BASELOAD_LB_CO2_PER_MWH * KG_PER_LB / 1000, 2),
        "homes_powered": round(energy / AVERAGE_HOME_KWH_PER_YEAR, 2),
        "sources": SOURCES,
        "notes": "Screening estimate: each kWh offsets one kWh at the state's average residential price; "
                 "no incentives, rate changes, net-metering rules or degradation are modeled.",
    }
