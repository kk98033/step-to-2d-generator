import unittest

from auto_2d_drawing.title_block import setup_document
from experimental_lab.tolerance_extraction_audit.common.scripts.engineering_dimension_renderer import (
    diameter_arrow_audit,
    render_annular_dimensions,
)


class EngineeringDimensionRendererTests(unittest.TestCase):
    def test_full_span_diameter_has_two_inward_arrowheads(self):
        document = setup_document()
        render_annular_dimensions(
            document,
            [{
                "dim_type": "DIAMETER",
                "nominal_value": 20.0,
                "anchors": [[-10.0, 0.0], [10.0, 0.0]],
                "label": "%%c20.00",
            }],
            map_point=lambda point: (float(point[0]), float(point[1])),
            drawing_bbox=(-20.0, -20.0, 20.0, 20.0),
        )
        dimension = list(document.modelspace().query("DIMENSION"))[0]
        audit = diameter_arrow_audit(dimension)
        self.assertEqual(audit["arrowhead_count"], 2)
        self.assertTrue(audit["two_arrowheads"])
        self.assertTrue(audit["arrowheads_point_inward"])


if __name__ == "__main__":
    unittest.main()
