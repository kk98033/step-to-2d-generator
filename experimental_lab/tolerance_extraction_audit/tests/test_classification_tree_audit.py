import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.run_classification_tree_audit import (
    classification_confidence,
    renderer_for_rule,
    structure_label,
)


class ClassificationTreeAuditTests(unittest.TestCase):
    def test_structure_label_does_not_claim_multibody_is_always_an_assembly(self):
        self.assertEqual(structure_label(1), "SINGLE_SOLID_PART")
        self.assertEqual(structure_label(3), "MULTI_BODY_OR_ASSEMBLY")

    def test_feature_semantics_route_to_shared_renderers(self):
        self.assertEqual(renderer_for_rule({"dim_type": "DIAMETER"}), "DIAMETER_RENDERER")
        self.assertEqual(renderer_for_rule({"dim_type": "LINEAR", "category": "thickness"}), "THICKNESS_RENDERER")
        self.assertEqual(renderer_for_rule({"dim_type": "HOLE_PATTERN"}), "PATTERN_DIMENSION_RENDERER")

    def test_hint_dependent_family_is_lower_confidence(self):
        agreed, _ = classification_confidence("SHAFT", "SHAFT")
        dependent, reason = classification_confidence("GENERAL_MECHANICAL", "FAN_HOUSING")
        self.assertGreater(agreed, dependent)
        self.assertEqual(reason, "HINT_OR_POLICY_DEPENDENT")


if __name__ == "__main__":
    unittest.main()
