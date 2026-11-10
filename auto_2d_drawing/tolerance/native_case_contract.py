"""Native case ingestion using the unchanged historical ToleranceCase schema."""
import copy
import hashlib
import json
import math
import re
import uuid

from auto_2d_drawing.tolerance.case_base import FeatureCaseBase, ToleranceCase
from auto_2d_drawing.tolerance.iso_tolerance_table import HOLE_FITS_TABLE, SHAFT_FITS_TABLE

SCHEMA = 'NATIVE_MEASUREMENT_V1'


def normalize_tolerance(config, text, nominal, kind, role):
    config = dict(config or {})
    mode = str(config.get('mode') or 'NONE').upper()
    if mode == 'NONE' and text:
        symmetric = re.fullmatch(r'\s*(?:±|\+/-)\s*(\d+(?:\.\d+)?)\s*(?:mm)?\s*', str(text))
        if symmetric:
            mode, config = 'CUSTOM_SYMMETRIC', {'dev': float(symmetric[1])}
        elif re.fullmatch(r'[A-Za-z]+\d+', str(text).strip()):
            mode, config = 'FIT', {'fit_class': str(text).strip()}
    if mode == 'FIT':
        if kind != 'DIAMETER':
            raise ValueError('配合公差只能用於已驗證的直徑尺寸')
        is_hole = 'INNER' in role or 'HOLE' in role
        fit = str(config.get('fit_class') or '')
        table = HOLE_FITS_TABLE if is_hole else SHAFT_FITS_TABLE
        exact = next((fits[fit] for low, high, fits in table if low < nominal <= high and fit in fits), None)
        if exact is None:
            raise ValueError('配合代號或尺寸超出已驗證查表範圍；不使用近似偏差')
        return {'mode': 'FIT', 'fit_class': fit, 'upper_dev': float(exact[0]), 'lower_dev': float(exact[1]), 'is_hole': is_hole}
    if mode == 'CUSTOM_SYMMETRIC':
        dev = float(config.get('dev', config.get('upper_dev', 0)))
        if not math.isfinite(dev) or dev <= 0:
            raise ValueError('對稱公差必須是有限正數')
        return {'mode': mode, 'dev': abs(dev)}
    if mode in {'CUSTOM_LIMITS', 'GROOVE'}:
        upper, lower = float(config.get('upper_dev', 0)), float(config.get('lower_dev', 0))
        if not all(math.isfinite(value) for value in (upper, lower)) or upper < lower or upper == lower:
            raise ValueError('公差上下限不合法')
        if mode == 'GROOVE' and kind != 'DIAMETER':
            raise ValueError('槽徑公差不可套用線性尺寸')
        return {'mode': mode, 'upper_dev': upper, 'lower_dev': lower}
    raise ValueError('請先在工程圖設定結構化公差，再將該尺寸加入案例庫')


def build_native_cases(report, records, *, owner_id, source_sha256, artifact_id, model_id, part_id, operation_id=None):
    from experimental_lab.tolerance_extraction_audit.TOLERANCE_V1_EXPERIMENT.engine import NativeToleranceEngine
    base = FeatureCaseBase.__new__(FeatureCaseBase)
    base.cases = []
    report['source_step_sha256'] = source_sha256
    selected = [record for record in records if record.get('enabled')]
    ids = [str(record.get('rule_id') or record.get('id') or '') for record in selected]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('請選擇不重複且已產圖的尺寸')
    result = NativeToleranceEngine(base).recommend_report(report, requested_ids=ids)
    nodes = {item['rule_id']: item for item in result['native_measurements']}
    intents = {item['intent_id']: item for item in report['dimension_intents']['intents']}
    payloads = []
    for saved in selected:
        rid = str(saved.get('rule_id') or saved.get('id'))
        if rid not in nodes:
            raise ValueError(f'{rid} 尚無可驗證的新版量測契約，不能加入推薦案例')
        native = nodes[rid]
        intent, node = intents[rid], native['node']
        measurement = intent['measurement']
        tolerance = normalize_tolerance(saved.get('tolerance_config'), saved.get('tolerance'),
            float(measurement['nominal_value']), measurement['kind'], measurement['role'])
        signature = {'owner_id': owner_id, 'source_sha256': source_sha256,
                     'intent_id': rid, 'tolerance': tolerance}
        digest = hashlib.sha256(json.dumps(signature, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        case_id = 'NATIVE_' + uuid.uuid5(uuid.NAMESPACE_URL, digest).hex
        metadata = {
            'case_contract': SCHEMA, 'extraction_engine': report['method'], 'feature_identity_verified': True,
            'geometry_verification': {'passed': True, 'checks': intent['verification']['checks']},
            'dimension_category': measurement['kind'], 'measurement_role': measurement['role'],
            'measurement_semantic': measurement.get('semantic'), 'unit': measurement.get('unit', 'mm'),
            'native_feature_id': native['native_feature_id'], 'native_intent_id': rid,
            'topology_refs': intent['feature'].get('topology_refs') or [],
            'display_geometry': intent['feature'].get('display_geometry'),
            'source_step_sha256': source_sha256, 'source_operation_id': operation_id,
            'source_artifact_id': artifact_id, 'model_id': model_id, 'part_id': part_id,
            'owner_user_id': owner_id, 'scope': 'PERSONAL', 'content_hash': digest,
            'product_family': FeatureCaseBase.infer_product_family(part_id) or '',
            'origin_case_id': case_id, 'drawing_file': f'NATIVE_{source_sha256[:24]}.dxf',
            'placement': {key: saved[key] for key in ('views', 'target_views', 'preferred_view', 'side', 'sides', 'baseline', 'offset') if key in saved},
        }
        case = ToleranceCase(case_id=case_id, part_type='GENERAL', feature_type=node['feature_type'],
            inferred_role=measurement['role'], nominal_dimensions=node['nominal'], neighbor_types=[],
            boundary_position='INTERIOR', tolerance_config=tolerance, confidence=1.0,
            evidence_source='ENGINEER_CONFIRMED', description='工程師明確確認並加入的新版尺寸案例',
            verification_status='ENGINEER_VERIFIED', source_metadata=metadata)
        # Strict round trip through exactly the historical loader's dataclass.
        payloads.append(ToleranceCase(**case.to_dict()).to_dict())
    return payloads


def historical_compatibility(case):
    """Assess without editing the original file, IDs, or verification evidence."""
    payload = copy.deepcopy(case.to_dict())
    metadata = payload['source_metadata']
    kind = FeatureCaseBase.canonical_dimension_category(str(metadata.get('dimension_category') or ''))
    feature = FeatureCaseBase.canonical_feature_type(case.feature_type)
    value = FeatureCaseBase.nominal_diameter(case.nominal_dimensions) if kind == 'DIAMETER' else FeatureCaseBase.nominal_length(case.nominal_dimensions)
    reason = None
    if kind not in {'LINEAR', 'DIAMETER'}:
        reason = 'LEGACY_MEASUREMENT_KIND_UNRESOLVED'
    elif not value or not math.isfinite(float(value)):
        reason = 'LEGACY_NOMINAL_FIELD_UNRESOLVED'
    elif feature not in {'shaft_segment', 'hole', 'overall_dimension', 'annular_rim', 'wall_thickness', 'retaining_ring_groove', 'locating_shoulder'}:
        reason = 'LEGACY_FEATURE_SEMANTIC_UNSUPPORTED'
    elif not case.is_verified_extraction():
        reason = 'LEGACY_GEOMETRY_IDENTITY_NOT_VERIFIED'
    metadata['native_compatibility'] = {'eligible': reason is None, 'reason': reason,
                                        'original_verification_status': case.verification_status}
    if reason and case.is_retrieval_eligible():
        payload['verification_status'] = 'REVIEW_CANDIDATE'
    return ToleranceCase(**payload)


def merge_compatible_cases(company_cases, extra_payloads):
    cases, origins = {}, set()
    for case in company_cases:
        adapted = historical_compatibility(case)
        if adapted.case_id in cases and adapted.to_dict() != cases[adapted.case_id].to_dict():
            raise ValueError('歷史案例 ID 衝突，已停止合併')
        cases[adapted.case_id] = adapted
    # Published snapshots precede private copies; one original is one vote.
    for payload in extra_payloads:
        case = ToleranceCase(**copy.deepcopy(payload))
        origin = case.source_metadata.get('origin_case_id') or case.case_id
        if origin in origins:
            continue
        origins.add(origin)
        if case.case_id in cases and case.to_dict() != cases[case.case_id].to_dict():
            raise ValueError('案例 ID 衝突，已停止合併')
        cases[case.case_id] = case
    base = FeatureCaseBase.__new__(FeatureCaseBase)
    base.cases = list(cases.values())
    base.strict_native_conflicts = True
    return base
