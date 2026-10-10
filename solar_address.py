"""Turn a lower-48 US address and radius into a Google Solar dataLayers request."""

import argparse
import getpass
import json
import math
import os
from pathlib import Path
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"
SOLAR_URL = "https://solar.googleapis.com/v1/dataLayers:get"
def local_api_key():
    """Read the ignored local key file without publishing credentials."""
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "GOOGLE_MAPS_API_KEY":
                return value.strip().strip("\"'")
    return ""


DEFAULT_API_KEY = local_api_key()
ALLOWED_STATES = set(
    "AL AZ AR CA CO CT DE FL GA ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT "
    "NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)


class InputError(ValueError):
    """An input cannot be used safely for this request."""


def https_context():
    """Use installed CA certificates, including the project's local environment."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        bundles = sorted((Path(__file__).resolve().parent / ".venv" / "lib").glob(
            "python*/site-packages/certifi/cacert.pem"
        ))
        if bundles:
            return ssl.create_default_context(cafile=str(bundles[-1]))
        return ssl.create_default_context()


def get_json(url, params):
    """Request JSON without printing a URL containing the API key."""
    try:
        with urlopen(url + "?" + urlencode(params), timeout=30, context=https_context()) as response:
            return json.load(response)
    except HTTPError as exc:
        # Google normally supplies a useful JSON error; never expose the keyed URL.
        try:
            detail = json.loads(exc.read()).get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            detail = ""
        raise InputError(f"Google API HTTP {exc.code}: {detail or 'Request failed.'}") from None
    except URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise InputError(
                "Python's HTTPS certificates are missing. Run: "
                "python3 -m venv .venv, then .venv/bin/python -m pip install certifi"
            ) from None
        raise InputError("Could not connect to Google. Check your connection and retry.") from None
    except (TimeoutError, OSError):
        raise InputError("Could not connect to Google. Check your connection and retry.") from None
    except ValueError:
        raise InputError("Google returned an invalid JSON response.") from None


def radius_in_meters(radius, unit):
    if not math.isfinite(radius) or radius <= 0:
        raise InputError("Radius must be a finite number greater than zero.")
    meters = radius * 0.3048 if unit == "feet" else radius
    if meters > 1000:
        raise InputError(
            "Radius exceeds Google's single-request limit of 1,000 meters "
            "(about 3,280.84 feet). A larger area requires multiple requests."
        )
    return meters


def validate_geocode(result):
    components = {
        kind: component.get("short_name")
        for component in result.get("address_components", [])
        for kind in component.get("types", [])
    }
    if (components.get("country") != "US"
            or components.get("administrative_area_level_1") not in ALLOWED_STATES):
        raise InputError(
            "Not allowed: the address must be in the lower 48 United States "
            "or Washington, DC. Alaska, Hawaii, and US territories are excluded."
        )
    if result.get("partial_match"):
        raise InputError("Address only partially matched. Enter a complete, correct street address.")
    if not {"street_number", "route"}.issubset(components):
        raise InputError("Enter a street address with a building number, city, and state.")
    location = result.get("geometry", {}).get("location", {})
    latitude, longitude = location.get("lat"), location.get("lng")
    if not all(isinstance(v, (int, float)) and math.isfinite(v)
               for v in (latitude, longitude)):
        raise InputError("Google did not return valid coordinates for this address.")
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise InputError("Google returned coordinates outside valid latitude/longitude ranges.")
    return latitude, longitude


def geocode_address(address, api_key):
    if not address.strip():
        raise InputError("Address cannot be empty.")
    # Do not country-filter: resolve foreign addresses honestly, then reject them.
    data = get_json(GEOCODING_URL, {"address": address, "key": api_key})
    if data.get("status") == "ZERO_RESULTS":
        raise InputError("Address not found. Enter a complete street address, city, state, and ZIP.")
    if data.get("status") != "OK":
        raise InputError(
            f"Geocoding failed ({data.get('status', 'unknown')}): "
            f"{data.get('error_message', 'Check your API key, enabled APIs, billing, and quota.')}"
        )
    results = data.get("results", [])
    if len(results) != 1:
        raise InputError("Address is ambiguous. Include the street number, city, state, and ZIP.")
    result = results[0]
    latitude, longitude = validate_geocode(result)
    return result.get("formatted_address", address), latitude, longitude


def solar_parameters(latitude, longitude, meters):
    """Choose supported resolution and layers without shrinking the requested radius."""
    pixel_size = next(size for size in (0.1, 0.25, 0.5, 1.0) if meters <= size * 1000)
    return {
        "location.latitude": latitude,
        "location.longitude": longitude,
        "radiusMeters": meters,
        "view": "FULL_LAYERS" if meters <= 175 else "IMAGERY_AND_ANNUAL_FLUX_LAYERS",
        "pixelSizeMeters": pixel_size,
        "requiredQuality": "HIGH",
    }


def prepare_request(address, radius, unit, api_key):
    """Reusable entry point for other Python code; returns JSON-compatible values."""
    if unit not in {"feet", "meters"}:
        raise InputError("Unit must be 'feet' or 'meters'.")
    meters = radius_in_meters(radius, unit)
    formatted, latitude, longitude = geocode_address(address, api_key)
    params = solar_parameters(latitude, longitude, meters)
    return {
        "address": formatted,
        "latitude": latitude,
        "longitude": longitude,
        "radius_meters": meters,
        "radius_feet": meters / 0.3048,
        "diameter_meters": meters * 2,
        "diameter_feet": meters * 2 / 0.3048,
        "solar_parameters": params,
        "solar_request_url": SOLAR_URL + "?" + urlencode(params) + "&key=YOUR_API_KEY",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", help="Complete street address, city, state, and ZIP")
    parser.add_argument("--radius", type=float, help="Distance from the center (not diameter)")
    parser.add_argument("--unit", choices=("feet", "meters"), help="Radius unit")
    parser.add_argument("--fetch-solar", action="store_true", help="Also call Solar API and include its JSON response")
    args = parser.parse_args()
    try:
        address = args.address if args.address is not None else input("US street address: ")
        radius = args.radius if args.radius is not None else float(input("Radius: "))
        unit = args.unit if args.unit is not None else input("Unit (feet/meters): ").strip().lower()
        # Validate local inputs before asking for credentials or making a billable call.
        if unit not in {"feet", "meters"}:
            raise InputError("Unit must be 'feet' or 'meters'.")
        radius_in_meters(radius, unit)
        api_key = os.environ.get("GOOGLE_MAPS_API_KEY", DEFAULT_API_KEY).strip()
        if not api_key:
            api_key = getpass.getpass("Google API key (hidden): ").strip()
        if not api_key:
            raise InputError("A Google API key is required for address lookup.")
        output = prepare_request(address, radius, unit, api_key)
        if args.fetch_solar:
            output["solar_data"] = get_json(SOLAR_URL, {**output["solar_parameters"], "key": api_key})
        print(json.dumps(output, indent=2))
        return 0
    except (InputError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("Input cancelled.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
