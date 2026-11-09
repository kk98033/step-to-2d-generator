import copy
import unittest
from unittest.mock import patch
from auto_2d_drawing.tolerance.case_base import FeatureCaseBase
from auto_2d_drawing.smart_extractors.experimental_candidate_adapter import EXPECTED_METHOD
from experimental_lab.tolerance_extraction_audit.TOLERANCE_V1_EXPERIMENT.engine import NativeToleranceEngine


def fixture():
    return {'mode': 'ANALYSIS_ONLY', 'method': EXPECTED_METHOD, 'dimension_intents': {'intents': [{
        'intent_id': 'native-overall',
        'feature': {'family': 'SOLID_SHAFT', 'feature_id': 'native-shaft', 'axis': [0, 1, 0],
                    'topology_refs': ['solid:1'], 'display_geometry': {
                        'kind': 'linear_span', 'start': [3, 7, 2], 'end': [3, 28.4, 2], 'radius': 1.5}},
        'measurement': {'kind': 'LINEAR', 'role': 'AXIAL_OVERALL_LENGTH', 'nominal_value': 21.4, 'unit': 'mm'},
        'projection': {'preferred_view': 'front', 'anchors': [[0, 0], [21.4, 0]]},
        'verification': {'status': 'AUTO_VERIFIED', 'checks': [{'passed': True}]},
        'source_candidates': [{'extractor': 'SOLID_SHAFT_V1', 'dimension_id': 'shaft.overall_length'}],
    }]}, 'detectors': {}}


class NativeToleranceTests(unittest.TestCase):
    def setUp(self):
        base = FeatureCaseBase.__new__(FeatureCaseBase)
        base.cases = []
        self.engine = NativeToleranceEngine(base)

    def test_native_overall_semantics_and_dynamic_axis(self):
        with patch.object(self.engine.policy.frg_extractor, 'build_graph', side_effect=AssertionError('legacy graph used')):
            result = self.engine.recommend_report(fixture())
        self.assertEqual(result['recommendations']['native-overall']['tolerance_config'], {'mode': 'CUSTOM_SYMMETRIC', 'dev': .1})
        self.assertEqual(result['native_measurements'][0]['node']['axial_span'], [7.0, 28.4])
        self.assertEqual(result['personal_history_policy'], 'DISABLED_DURING_EXPERIMENT')

    def test_unverified_and_failed_geometry_never_recommend(self):
        for change in ['status', 'checks', 'unit']:
            report = fixture()
            intent = report['dimension_intents']['intents'][0]
            if change == 'status': intent['verification']['status'] = 'REVIEW_CANDIDATE'
            if change == 'checks': intent['verification']['checks'] = [{'passed': False}]
            if change == 'unit': intent['measurement']['unit'] = 'inch'
            result = self.engine.recommend_report(report)['recommendations']['native-overall']
            self.assertEqual(result['recommended_mode'], 'NONE')
            self.assertEqual(result['tier_level'], 'NOT_APPLICABLE')

    def test_unknown_client_feature_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'UNKNOWN_NATIVE_MEASUREMENT'):
            self.engine.recommend_report(fixture(), requested_ids=['invented'])

    def test_client_category_cannot_change_measurement_semantics(self):
        report = fixture()
        # Presentation strings never choose overall-vs-shaft fallback policy.
        report['dimension_intents']['intents'][0]['category'] = 'hole'
        actual = self.engine.recommend_report(report)['recommendations']['native-overall']
        self.assertEqual(actual['feature_type'], 'overall')
