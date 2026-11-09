"""Native dimension-intent tolerance experiment; no legacy feature bridge."""
from __future__ import annotations

import math
from pathlib import Path

from auto_2d_drawing.smart_extractors.experimental_candidate_adapter import build_experimental_candidate_rules
from auto_2d_drawing.tolerance.case_base import FeatureCaseBase
from auto_2d_drawing.tolerance.feature_graph import FeatureNode
from auto_2d_drawing.tolerance.tolerance_decision_service import ToleranceDecisionService, ToleranceRecommendation

ENGINE_VERSION = 'native-tolerance-experimental-v1'


def measurement_category(role, kind):
    """Measurement semantics, never the drawing UI's category string."""
    if kind == 'DIAMETER':
        return 'hole' if 'INNER' in role else 'shaft'
    if 'OVERALL' in role:
        return 'overall'
    return 'step'


class NativeToleranceEngine:
    def __init__(self, case_base=None):
        # Only the evidence/ISO decision kernel is reused. Neither build_graph
        # nor legacy candidate extraction/matching is invoked by this engine.
        self.policy = ToleranceDecisionService(case_base=case_base or FeatureCaseBase())

    def recommend_measurement(self, node, rule_id, nominal, kind, role, part_type,
                              product_family=None, allowed_case_ids=None):
        return self.policy._evaluate_recommendation(
            rule_id, measurement_category(role, kind), kind == 'DIAMETER', nominal,
            node, part_type, product_family, dimension_category=kind,
            allowed_case_ids=allowed_case_ids,
        ).to_dict()

    @staticmethod
    def blocked(rule, reason):
        result = ToleranceRecommendation(
            rule_id=rule['rule_id'], feature_type=rule.get('feature_family') or 'relation',
            inferred_role=rule.get('measurement_role') or 'UNKNOWN',
            nominal_value=float(rule.get('nominal_value') or 0), recommended_mode='NONE',
            tier_level='NOT_APPLICABLE', decision_status='NOT_APPLICABLE',
            confidence_basis='NATIVE_MEASUREMENT_VALIDATION',
            retrieval_trace={'decision_source': 'NATIVE_INTENT_GUARD', 'block_reason': reason},
            reasoning_description='原生尺寸證據或歷史量測契約不足；不自動產生公差。',
        ).to_dict()
        return result

    def recommend_report(self, report, *, part_type='GENERAL', product_family=None,
                         allowed_case_ids=None, requested_ids=None):
        rules = build_experimental_candidate_rules(report)
        known = {rule['rule_id'] for rule in rules}
        requested = known if requested_ids is None else set(requested_ids)
        if requested - known:
            raise ValueError('UNKNOWN_NATIVE_MEASUREMENT: ' + ', '.join(sorted(requested - known)))
        intents = {item['intent_id']: item for item in (report.get('dimension_intents') or {}).get('intents') or []}
        recommendations, measurements = {}, []
        for rule in rules:
            rid = rule['rule_id']
            if rid not in requested:
                continue
            intent = intents.get(rid)
            reason = None
            if not intent:
                reason = 'RELATION_HISTORY_CONTRACT_NOT_VALIDATED'
            else:
                verification = intent.get('verification') or {}
                checks = verification.get('checks') or []
                measurement = intent.get('measurement') or {}
                nominal = float(measurement.get('nominal_value') or 0)
                if (verification.get('status') != 'AUTO_VERIFIED' or not checks
                        or any(check.get('passed') is not True for check in checks)
                        or not math.isfinite(nominal) or nominal <= 0):
                    reason = 'NATIVE_GEOMETRY_NOT_VERIFIED'
                elif measurement.get('unit', 'mm') != 'mm' or measurement.get('kind') not in {'LINEAR', 'DIAMETER'}:
                    reason = 'DIMENSION_KIND_OR_UNIT_NOT_VALIDATED'
            if reason:
                recommendations[rid] = self.blocked(rule, reason)
                continue
            feature = intent['feature']
            measurement = intent['measurement']
            kind, role = measurement['kind'], measurement['role']
            family = str(feature.get('family') or '')
            if 'OVERALL' in role:
                feature_type = 'overall_dimension'
            elif family == 'SOLID_SHAFT':
                feature_type = 'shaft_segment'
            elif 'ANNULAR' in family:
                feature_type = 'annular_rim'
            elif 'THICKNESS' in role:
                feature_type = 'wall_thickness'
            else:
                recommendations[rid] = self.blocked(rule, 'NATIVE_FEATURE_FAMILY_NOT_VALIDATED')
                continue
            nominal = float(measurement['nominal_value'])
            nominal_fields = {'diameter' if kind == 'DIAMETER' else 'length': nominal}
            geometry = feature.get('display_geometry') or {}
            if kind == 'LINEAR' and family == 'SOLID_SHAFT' and geometry.get('radius'):
                nominal_fields['diameter'] = 2 * float(geometry['radius'])
            axis = feature.get('axis') or []
            points = [geometry.get('start'), geometry.get('end')]
            if not all(isinstance(point, (list, tuple)) and len(point) == 3 for point in points) or len(axis) != 3:
                recommendations[rid] = self.blocked(rule, 'NATIVE_3D_GEOMETRY_MISSING')
                continue
            norm = math.sqrt(sum(float(value) ** 2 for value in axis))
            if norm <= 1e-12:
                recommendations[rid] = self.blocked(rule, 'NATIVE_AXIS_INVALID')
                continue
            positions = [sum(float(point[i]) * float(axis[i]) / norm for i in range(3)) for point in points]
            node = FeatureNode(
                id=rid, feature_type=feature_type, nominal=nominal_fields,
                axial_span=sorted(positions), center_axial=sum(positions) / 2,
                inferred_role=role,
                source_info={'native_feature_id': feature.get('feature_id'),
                             'topology_refs': feature.get('topology_refs'),
                             'measurement_semantic': measurement.get('semantic')},
            )
            recommendations[rid] = self.recommend_measurement(
                node, rid, nominal, kind, role, part_type, product_family, allowed_case_ids,
            )
            measurements.append({'rule_id': rid, 'native_feature_id': feature.get('feature_id'),
                                 'semantic': measurement.get('semantic'), 'node': node.to_dict()})
        return {
            'status': 'ok', 'tolerance_engine': ENGINE_VERSION, 'part_type': part_type,
            'product_family': product_family or '', 'total_rules': len(recommendations),
            'high_confidence_count': sum(rec['confidence'] >= .85 for rec in recommendations.values()),
            'recommendations': recommendations,
            'native_measurements': measurements,
            'personal_history_policy': 'DISABLED_DURING_EXPERIMENT',
            'limitations': ['角度與複合關係尺寸尚未通過歷史公差驗證，維持不推薦。',
                            '實驗推薦需工程師覆核；固定基準為 SILVER，尚非簽核 GOLD。'],
        }


def recommend_step(step_path, **options):
    from experimental_lab.tolerance_extraction_audit.DRAWING_CURRENT_FINAL.experimental_main import analyse_step
    return NativeToleranceEngine().recommend_report(analyse_step(Path(step_path)), **options)
