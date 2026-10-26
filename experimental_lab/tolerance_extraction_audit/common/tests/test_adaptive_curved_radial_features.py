import sys
import unittest
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from adaptive_curved_radial_features import _periodic_fits


def edge_family(count, offsets, missing=None):
    missing = set(missing or [])
    rows = []
    for index in range(count):
        if index in missing:
            continue
        for offset in offsets:
            rows.append({"inner_angle": (index * 360.0 / count + offset) % 360.0})
    return rows


class AdaptiveCurvedRadialFeatureTests(unittest.TestCase):
    def test_recovers_59_repeat_from_two_fragment_families_with_missing_slots(self):
        fits = _periodic_fits(edge_family(59, (0.0, 1.1), missing={7, 31}))
        self.assertIn(59, {item["pattern_count"] for item in fits})

    def test_exposes_72_as_harmonic_candidate_from_four_fragment_families(self):
        fits = _periodic_fits(edge_family(72, (0.0, 1.0, 2.4, 3.2)))
        self.assertIn(72, {item["pattern_count"] for item in fits})

    def test_keeps_single_boundary_71_repeat(self):
        fits = _periodic_fits(edge_family(71, (0.0,)))
        self.assertEqual(fits[0]["pattern_count"], 71)


if __name__ == "__main__":
    unittest.main()
