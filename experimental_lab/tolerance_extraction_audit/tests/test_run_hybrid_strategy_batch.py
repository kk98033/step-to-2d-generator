import unittest
from types import SimpleNamespace

from experimental_lab.tolerance_extraction_audit.common.scripts.run_hybrid_strategy_batch import (
    dimension_evidence_crop,
)


class HybridStrategyBatchTests(unittest.TestCase):
    def test_dimension_crop_ignores_origin_placeholders(self):
        dimensions = [
            SimpleNamespace(points={
                "defpoint": [10.0, 20.0],
                "defpoint2": [30.0, 40.0],
                "defpoint4": [0.0, 0.0],
            })
        ]
        xmin, ymin, xmax, ymax = dimension_evidence_crop(dimensions)
        self.assertLess(xmin, 10.0)
        self.assertLess(ymin, 20.0)
        self.assertGreater(xmax, 30.0)
        self.assertGreater(ymax, 40.0)
        self.assertGreater(xmin, 0.0)


if __name__ == "__main__":
    unittest.main()
