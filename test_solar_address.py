"""Offline checks for geographic validation and Google's request boundaries."""

import unittest
from unittest.mock import patch

from solar_address import InputError, prepare_request, radius_in_meters, solar_parameters, validate_geocode


def geocode(country="US", state="CA", **extra):
    return {
        "formatted_address": "123 Example Street, Example City",
        "address_components": [
            {"short_name": country, "types": ["country"]},
            {"short_name": state, "types": ["administrative_area_level_1"]},
            {"short_name": "123", "types": ["street_number"]},
            {"short_name": "Example St", "types": ["route"]},
        ],
        "geometry": {"location": {"lat": 37.42, "lng": -122.08}},
        **extra,
    }


class SolarAddressTests(unittest.TestCase):
    def test_conversion(self):
        self.assertAlmostEqual(radius_in_meters(300, "feet"), 91.44)
        self.assertEqual(radius_in_meters(100, "meters"), 100)

    def test_invalid_radii(self):
        for value in (0, -1, float("inf"), float("nan"), 1000.001):
            with self.subTest(value=value), self.assertRaises(InputError):
                radius_in_meters(value, "meters")

    def test_allowed_regions(self):
        for state in ("CA", "NJ", "DC"):
            with self.subTest(state=state):
                self.assertEqual(validate_geocode(geocode(state=state)), (37.42, -122.08))

    def test_disallowed_regions(self):
        for country, state in (("US", "AK"), ("US", "HI"), ("US", "PR"), ("CA", "ON"), ("US", None)):
            with self.subTest(country=country, state=state), self.assertRaisesRegex(InputError, "Not allowed"):
                validate_geocode(geocode(country, state))

    def test_partial_and_non_street_matches(self):
        with self.assertRaisesRegex(InputError, "partially"):
            validate_geocode(geocode(partial_match=True))
        result = geocode()
        result["address_components"].pop()
        with self.assertRaisesRegex(InputError, "street address"):
            validate_geocode(result)

    def test_solar_boundaries(self):
        for radius, size, full in ((100, .1, True), (100.001, .25, True), (175, .25, True),
                                   (175.001, .25, False), (250, .25, False),
                                   (250.001, .5, False), (500, .5, False),
                                   (500.001, 1, False), (1000, 1, False)):
            with self.subTest(radius=radius):
                params = solar_parameters(37.42, -122.08, radius)
                self.assertEqual(params["pixelSizeMeters"], size)
                self.assertEqual(params["view"] == "FULL_LAYERS", full)
                self.assertEqual(params["radiusMeters"], radius)

    @patch("solar_address.get_json")
    def test_complete_output_and_secret_redaction(self, request):
        request.return_value = {"status": "OK", "results": [geocode()]}
        output = prepare_request("123 Example St", 300, "feet", "secret-key")
        self.assertEqual(output["latitude"], 37.42)
        self.assertAlmostEqual(output["radius_meters"], 91.44)
        self.assertAlmostEqual(output["diameter_feet"], 600)
        self.assertIn("radiusMeters=", output["solar_request_url"])
        self.assertNotIn("secret-key", str(output))

    @patch("solar_address.get_json")
    def test_invalid_input_does_not_call_google(self, request):
        with self.assertRaises(InputError):
            prepare_request("123 Example St", 1001, "meters", "key")
        request.assert_not_called()

    @patch("solar_address.get_json")
    def test_ambiguous_missing_and_denied(self, request):
        for response in ({"status": "OK", "results": [geocode(), geocode()]},
                         {"status": "ZERO_RESULTS"}, {"status": "REQUEST_DENIED"}):
            with self.subTest(response=response), self.assertRaises(InputError):
                request.return_value = response
                prepare_request("example", 100, "meters", "key")


if __name__ == "__main__":
    unittest.main()
