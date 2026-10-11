import unittest
import numpy as np
from shapely import affinity
from shapely.geometry import box, mapping, shape

from panel_plan import DEFAULT_SYSTEM_DERATE, propose_panels, validate_plan_inputs
from solar_address import InputError


def fixture():
    return {"analysis": {"resolution_m": 1},
            # Losses off so these tests check geometry and budget math exactly.
            "assumptions": {"panel_kw_per_m2": .2, "usable_roof_fraction": .65, "system_derate": 1},
            "roofs": [{"preview_geometry": mapping(box(0, 0, 20, 20)),
                       "properties": {"id": "roof-1", "area_m2": 400,
                                      "flux_coverage_percent": 100, "annual_yield_kwh_per_kw": 1000}}]}


def sunlight_tables(flux):
    valid = np.isfinite(flux)
    table = lambda values: np.pad(values.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    return {"sum": table(np.where(valid, flux, 0)), "count": table(valid.astype(float)), "shape": flux.shape}


class AccuracyTests(unittest.TestCase):
    def test_system_losses_reduce_energy_by_the_derate(self):
        data = fixture(); del data["assumptions"]["system_derate"]
        plan = propose_panels(data, budget_usd=1200, energy_target_kwh_year=1000, installed_cost_per_watt=3)
        self.assertAlmostEqual(plan["estimated_energy_kwh_year"], 400 * DEFAULT_SYSTEM_DERATE)

    def test_panels_follow_a_rotated_roof(self):
        data = fixture()
        roof = affinity.rotate(box(0, 0, 30, 12), 30, origin=(15, 6))
        data["roofs"][0]["preview_geometry"] = mapping(roof)
        data["roofs"][0]["properties"]["area_m2"] = roof.area
        plan = propose_panels(data, budget_usd=100000, energy_target_kwh_year=100000, installed_cost_per_watt=3)
        self.assertGreater(plan["panel_count"], 0)
        panels = [shape(panel["preview_geometry"]) for panel in plan["panels"]]
        self.assertTrue(all(roof.buffer(1e-6).covers(panel) for panel in panels))
        # Edges run along the roof (about 30 degrees), and module size is kept.
        corners = list(panels[0].exterior.coords)
        angle = np.degrees(np.arctan2(corners[1][1] - corners[0][1], corners[1][0] - corners[0][0])) % 90
        self.assertTrue(min(abs(angle - 30), abs(angle - 60)) < 1, angle)
        self.assertEqual(sorted((plan["panels"][0]["width_m"], plan["panels"][0]["height_m"])), [1, 2])

    def test_sunniest_spots_fill_first(self):
        data = fixture()
        flux = np.full((20, 20), 1000.0); flux[:, 12:] = 1500
        plan = propose_panels(data, budget_usd=3600, energy_target_kwh_year=100000, installed_cost_per_watt=3,
                              sunlight=sunlight_tables(flux))
        self.assertEqual(plan["panel_count"], 3)
        self.assertTrue(all(shape(panel["preview_geometry"]).bounds[0] >= 12 for panel in plan["panels"]))
        self.assertAlmostEqual(plan["estimated_energy_kwh_year"], 3 * .4 * 1500)


class PanelPlanTests(unittest.TestCase):
    def test_budget_constraint_and_shortfall(self):
        plan = propose_panels(fixture(), budget_usd=1200, energy_target_kwh_year=1000, installed_cost_per_watt=3)
        self.assertEqual(plan["panel_count"], 1)
        self.assertEqual(plan["estimated_cost_usd"], 1200)
        self.assertEqual(plan["estimated_energy_kwh_year"], 400)
        self.assertEqual(plan["energy_shortfall_kwh_year"], 600)
        self.assertFalse(plan["target_met"])

    def test_target_stops_selection_and_panels_fit_without_overlap(self):
        data = fixture()
        plan = propose_panels(data, budget_usd=10000, energy_target_kwh_year=1000, installed_cost_per_watt=3)
        self.assertTrue(plan["target_met"])
        self.assertEqual(plan["panel_count"], 3)
        self.assertEqual(plan["estimated_cost_usd"], 3600)
        polygons = [shape(panel["preview_geometry"]) for panel in plan["panels"]]
        roof = shape(data["roofs"][0]["preview_geometry"])
        for index, panel in enumerate(polygons):
            self.assertTrue(roof.covers(panel))
            self.assertAlmostEqual(panel.area, 2)
            for previous in polygons[:index]:
                self.assertEqual(panel.intersection(previous).area, 0)

    def test_zero_panels_when_budget_or_coverage_insufficient(self):
        data = fixture()
        self.assertEqual(propose_panels(data, budget_usd=1199)["panel_count"], 0)
        data["roofs"][0]["properties"]["flux_coverage_percent"] = 0
        self.assertEqual(propose_panels(data)["panel_count"], 0)

    def test_missing_yield_is_not_used(self):
        data = fixture()
        data["roofs"][0]["properties"]["annual_yield_kwh_per_kw"] = None
        self.assertEqual(propose_panels(data)["panel_count"], 0)

    def test_invalid_inputs(self):
        for field in ("budget_usd", "energy_target_kwh_year", "installed_cost_per_watt"):
            for value in (0, -1, float("nan"), float("inf"), "bad", None):
                with self.subTest(field=field, value=value), self.assertRaises(InputError):
                    validate_plan_inputs(**{field: value})


if __name__ == "__main__":
    unittest.main()
