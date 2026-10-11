import copy
import unittest
from unittest.mock import patch
from shapely.geometry import box, mapping
from roof_details import add_roof_addresses, describe_results
from solar_address import InputError


def fixture():
    return {
        "request": {"radius_meters": 100, "address": "123 Search St"},
        "analysis": {"flux_coverage_percent": 90},
        "roofs": [
            {"geometry": mapping(box(0, 0, 2, 2)), "properties": {"id": "roof-1", "estimated_energy_kwh_year": 12000}}
        ],
    }


class RoofDetailsTests(unittest.TestCase):
    @patch("roof_details.get_json")
    def test_matching_address_and_no_repeat_calls(self, get_json):
        get_json.return_value = {
            "status": "OK",
            "results": [{"formatted_address": "123 Roof St", "geometry": {"location": {"lat": 1, "lng": 1}}}],
        }
        data = fixture()
        add_roof_addresses(data, "key")
        self.assertEqual(data["roofs"][0]["properties"]["address"], "123 Roof St")
        self.assertEqual(data["roofs"][0]["properties"]["address_status"], "reverse_geocoded")
        add_roof_addresses(data, "key")
        self.assertEqual(get_json.call_count, 1)
        self.assertEqual(get_json.call_args.args[1]["result_type"], "street_address|premise")

    @patch("roof_details.get_json")
    def test_nearby_address_marked_approximate(self, get_json):
        get_json.return_value = {
            "status": "OK",
            "results": [{"formatted_address": "999 Other St", "geometry": {"location": {"lat": 10, "lng": 10}}}],
        }
        data = fixture()
        add_roof_addresses(data, "key")
        self.assertEqual(data["roofs"][0]["properties"]["address_status"], "nearby_address")
        describe_results(data)
        self.assertIn("address approximate", data["report"]["findings"])

    @patch("roof_details.get_json")
    def test_lookup_failure_preserves_roofs_and_stops_repeated_requests(self, get_json):
        get_json.side_effect = InputError("Network failed")
        data = fixture()
        data["roofs"].append(copy.deepcopy(data["roofs"][0]))
        add_roof_addresses(data, "key")
        self.assertEqual(get_json.call_count, 1)
        self.assertEqual(len(data["roofs"]), 2)
        self.assertTrue(all(r["properties"]["address"] == "Address unavailable" for r in data["roofs"]))

    @patch("roof_details.get_json")
    def test_demo_uses_only_synthetic_addresses(self, get_json):
        data = fixture()
        data["demo"] = True
        add_roof_addresses(data)
        describe_results(data)
        get_json.assert_not_called()
        self.assertIn("synthetic", data["roofs"][0]["properties"]["address"])
        self.assertIn("synthetic", data["report"]["findings"])

    def test_report_includes_plan_shortfall_and_empty_results(self):
        data = fixture()
        data["proposal"] = {
            "panel_count": 4,
            "estimated_cost_usd": 4800,
            "estimated_energy_kwh_year": 2000,
            "energy_shortfall_kwh_year": 8000,
            "target_met": False,
        }
        describe_results(data)
        self.assertIn("$4,800", data["report"]["findings"])
        self.assertIn("8,000", data["report"]["findings"])
        self.assertIn("larger budget", data["report"]["next_steps"])
        data["roofs"] = []
        describe_results(data)
        self.assertIn("0 candidate", data["report"]["findings"])
        self.assertIn("another address", data["report"]["next_steps"])


if __name__ == "__main__":
    unittest.main()
