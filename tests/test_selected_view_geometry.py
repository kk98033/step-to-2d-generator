import math
import unittest
from auto_2d_drawing.smart_extractors.selected_view_geometry import reproject_measurement, supported_intent_views
from auto_2d_drawing.smart_extractors.experimental_candidate_adapter import build_experimental_selection_config


class SelectedViewTests(unittest.TestCase):
    def candidate(self, kind='LINEAR'):
        return {'view': 'front', 'dim_type': kind, 'nominal_value': 10 if kind == 'LINEAR' else 4,
                'display_geometry': {'start': [3, 7, 11], 'end': [3, 17, 11], 'radius': 2}}

    def test_axial_measurement_is_reprojected_and_end_view_is_rejected(self):
        source = self.candidate()
        result = reproject_measurement(source, 'right')
        self.assertEqual(result['view'], 'right')
        self.assertAlmostEqual(math.dist(*result['anchors']), 10)
        self.assertNotIn('anchors', source)
        with self.assertRaisesRegex(ValueError, 'NOT_VISIBLE_AT_TRUE_LENGTH'):
            reproject_measurement(source, 'top')

    def test_diameter_is_visible_in_end_view(self):
        result = reproject_measurement(self.candidate('DIAMETER'), 'top')
        self.assertAlmostEqual(math.dist(*result['anchors']), 4)
        self.assertEqual(result['view'], 'top')

    def test_multiple_views_and_sides_expand_without_default_front(self):
        rule = {'rule_id': 'shaft', 'render_eligible': True, 'experimental_selection_group': 'shaft_dimension_selections',
                'experimental_selection_id': 'shaft.length', 'preferred_view': 'front', 'nominal_value': 10,
                'supported_views': ['front', 'right']}
        submitted = {**rule, 'enabled': True, 'target_views': ['right'], 'views': ['right'], 'sides': ['TOP', 'BOTTOM']}
        result = build_experimental_selection_config([rule], [submitted], part_hint='shaft')
        self.assertEqual([(item['view'], item['side']) for item in result['shaft_dimension_selections']], [('right', 'TOP'), ('right', 'BOTTOM')])
        submitted['target_views'] = []
        self.assertEqual(len(build_experimental_selection_config([rule], [submitted], part_hint='shaft')['selection_summary']['rejected']), 1)

    def test_supported_views_follow_axis_and_measurement(self):
        intent = {'feature': {'family': 'SOLID_SHAFT', 'axis': [0, 1, 0]},
                  'measurement': {'kind': 'LINEAR', 'role': 'AXIAL_OVERALL_LENGTH'}}
        self.assertEqual(supported_intent_views(intent), ['front', 'right'])
        intent['measurement']['kind'] = 'DIAMETER'
        self.assertEqual(supported_intent_views(intent), ['front', 'top', 'right'])
