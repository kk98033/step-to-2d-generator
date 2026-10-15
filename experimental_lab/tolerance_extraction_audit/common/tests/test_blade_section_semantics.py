import sys
import unittest
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blade_section_semantics import infer_rotation_axis


class BladeSectionSemanticTests(unittest.TestCase):
    def test_thinnest_bbox_extent_selects_z_axis(self):
        result = infer_rotation_axis((-30.0, -30.0, -2.0, 30.0, 30.0, 2.0))
        self.assertEqual(result["axis_index"], 2)
        self.assertEqual(result["radial_axes"], [0, 1])

    def test_axis_selection_is_translation_invariant(self):
        result = infer_rotation_axis((100.0, -5.0, 20.0, 106.0, 55.0, 80.0))
        self.assertEqual(result["axis_index"], 0)
        self.assertEqual(result["center"], [103.0, 25.0, 50.0])


if __name__ == "__main__":
    unittest.main()
