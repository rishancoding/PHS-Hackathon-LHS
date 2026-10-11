"""Color-based existing-panel screening: flags textured slate-blue arrays, not shadows or plain roofs."""

import tempfile
import unittest

import numpy as np
from shapely.geometry import box, shape
from shapely.ops import unary_union

from demo_roofready import run_demo
from existing_panels import detect_existing_panels
from panel_recommendations import build_recommendations

ROOF = (175, 172, 168)
PANEL = (118, 132, 162)


def scene(patch_color=None, textured=True, size=200):
    """20 m x 20 m roof at 0.1 m with an optional 6 m x 4 m patch."""
    rgb = np.empty((3, size, size), dtype=np.float32)
    rgb[:] = np.array(ROOF, dtype=np.float32)[:, None, None]
    if patch_color is not None:
        rgb[:, 60:100, 50:110] = np.array(patch_color, dtype=np.float32)[:, None, None]
        if textured:
            # Light module frames every ~1 m, as in 0.1 m aerial imagery.
            rgb[:, 60:100:10, 50:110] += 60
            rgb[:, 60:100, 50:110:17] += 60
    labels = np.zeros((size, size), dtype=np.int32)
    labels[10:190, 10:190] = 7
    return rgb, labels


class ExistingPanelTests(unittest.TestCase):
    def test_textured_blue_array_is_flagged_on_its_roof(self):
        rgb, labels = scene(PANEL)
        found = detect_existing_panels(rgb, labels, 0.1)
        self.assertEqual(len(found), 1)
        polygon, roof_label, area = found[0]
        self.assertEqual(roof_label, 7)
        self.assertTrue(polygon.buffer(1e-6).covers(box(50, 60, 110, 100)))
        self.assertGreater(area, 24)

    def test_shadows_plain_roofs_and_smooth_blue_are_not_flagged(self):
        for color, textured in ((None, False), ((40, 52, 82), False), ((40, 52, 82), True), (PANEL, False)):
            with self.subTest(color=color, textured=textured):
                rgb, labels = scene(color, textured)
                self.assertEqual(detect_existing_panels(rgb, labels, 0.1), [])

    def test_off_roof_blue_is_ignored(self):
        rgb, labels = scene(PANEL)
        labels[:] = 0
        self.assertEqual(detect_existing_panels(rgb, labels, 0.1), [])

    def test_new_layout_avoids_flagged_area_and_findings_say_so(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_demo(directory)
            build_recommendations(result)
        self.assertEqual(len(result["existing_panels"]), 1)
        flagged = unary_union([shape(f["preview_geometry"]) for f in result["existing_panels"]])
        drawn = box(34, 124, 44, 130)
        self.assertTrue(flagged.buffer(1e-6).covers(drawn))
        self.assertGreater(result["proposal"]["panel_count"], 0)
        self.assertFalse(any(shape(p["preview_geometry"]).intersects(flagged) for p in result["proposal"]["panels"]))
        self.assertIn("Possible existing solar panels", result["report"]["findings"])
        roof = next(
            r for r in result["roofs"] if r["properties"]["id"] == result["existing_panels"][0]["properties"]["roof_id"]
        )
        self.assertGreater(roof["properties"]["existing_panel_area_m2"], 0)


if __name__ == "__main__":
    unittest.main()
