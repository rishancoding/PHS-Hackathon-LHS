import unittest
from installation_cost import estimate_installation_cost
from panel_plan import propose_panels
from test_panel_plan import fixture


class InstallationCostTests(unittest.TestCase):
    def test_state_controls_regional_estimate(self):
        west = estimate_installation_cost({"request": {"state": "CA"}})
        south = estimate_installation_cost({"request": {"state": "TX"}})
        self.assertEqual(west["region"], "West")
        self.assertGreater(west["installed_cost_per_watt"], south["installed_cost_per_watt"])
        self.assertLess(west["low_cost_per_watt"], west["installed_cost_per_watt"])

    def test_older_saved_address_and_missing_state(self):
        estimate = estimate_installation_cost({"request": {"address": "123 Example St, Boston, MA 02110, USA"}})
        self.assertEqual(estimate["state"], "MA")
        self.assertEqual(estimate["region"], "Northeast")
        self.assertEqual(estimate_installation_cost({"request": {}})["installed_cost_per_watt"], 3.15)

    def test_automatic_cost_is_used_for_budget_and_report(self):
        data = fixture()
        data["request"] = {"state": "CA"}
        plan = propose_panels(data, budget_usd=2500, energy_target_kwh_year=10000)
        self.assertEqual(plan["panel_count"], 1)
        self.assertEqual(plan["installation_estimate"]["region"], "West")
        self.assertAlmostEqual(plan["estimated_cost_usd"], 0.4 * 1000 * 3.402)
        self.assertLessEqual(plan["estimated_cost_usd"], 2500)


if __name__ == "__main__":
    unittest.main()
