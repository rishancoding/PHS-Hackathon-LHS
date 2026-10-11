"""Server-side Google address predictions; never expose credentials or persist predictions."""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from solar_address import InputError, https_context

AUTOCOMPLETE_URL = 'https://places.googleapis.com/v1/places:autocomplete'


def address_predictions(query, api_key):
    if not isinstance(query, str) or not 3 <= len(query.strip()) <= 200:
        raise InputError('Type between 3 and 200 characters for address suggestions.')
    # Country plus geographic restriction excludes Alaska, Hawaii and US territories.
    # Final lower-48/state validation still runs through Geocoding before solar analysis.
    payload = {
        'input': query.strip(),
        'includedRegionCodes': ['us'],
        'languageCode': 'en',
        'locationRestriction': {
            'rectangle': {'low': {'latitude': 24, 'longitude': -125}, 'high': {'latitude': 50, 'longitude': -66}}
        },
    }
    request = Request(
        AUTOCOMPLETE_URL,
        data=json.dumps(payload).encode(),
        method='POST',
        headers={
            'Content-Type': 'application/json',
            'X-Goog-Api-Key': api_key,
            'X-Goog-FieldMask': 'suggestions.placePrediction.text.text,suggestions.placePrediction.structuredFormat',
        },
    )
    try:
        with urlopen(request, timeout=8, context=https_context()) as response:
            data = json.load(response)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise InputError(
                'Live suggestions are unavailable. Enable Places API (New) for your Google key; you can still type a full address.'
            ) from None
        raise InputError('Live suggestions are unavailable right now. You can still type a full address.') from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise InputError('Could not load live suggestions. You can still type a full address.') from None
    if not isinstance(data, dict) or 'error' in data or not isinstance(data.get('suggestions', []), list):
        raise InputError('Could not load live suggestions. You can still type a full address.')
    items = []
    for suggestion in data.get('suggestions', []):
        if not isinstance(suggestion, dict):
            continue
        prediction = suggestion.get('placePrediction') or {}
        if not isinstance(prediction, dict):
            continue

        def text_value(value):
            text = value.get('text') if isinstance(value, dict) else None
            return text if isinstance(text, str) else ''

        text = text_value(prediction.get('text'))
        if not isinstance(text, str) or not text.strip():
            continue
        formatted = prediction.get('structuredFormat') or {}
        if not isinstance(formatted, dict):
            formatted = {}
        items.append(
            {
                'address': text,
                'label': text_value(formatted.get('mainText')) or text,
                'detail': text_value(formatted.get('secondaryText')),
                'source': 'google',
            }
        )
    # Per-request autocomplete, followed by existing Geocoding on Analyze.
    # No incomplete Places session is presented as discounted session billing.
    return items[:5]
