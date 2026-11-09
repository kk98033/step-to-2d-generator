"""Reproducible bounded validation, with explicit precision and coverage gates."""
import json
import sys
import hashlib
import io
import unittest
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from auto_2d_drawing.tolerance.case_base import FeatureCaseBase
from auto_2d_drawing.tolerance.benchmark import BenchmarkDataset, ToleranceBenchmarkRunner, _case_group, _record_node, tolerance_equal
from auto_2d_drawing.tolerance.tolerance_decision_service import ToleranceDecisionService
from experimental_lab.tolerance_extraction_audit.TOLERANCE_V1_EXPERIMENT.engine import NativeToleranceEngine, recommend_step, ENGINE_VERSION


def run():
    report_path = Path(__file__).parent / 'validation_report.json'
    report_path.write_text('{"status": "VALIDATING"}', encoding='utf-8')
    suite = unittest.defaultTestLoader.loadTestsFromName(
        'experimental_lab.tolerance_extraction_audit.TOLERANCE_V1_EXPERIMENT.test_engine')
    safety = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
    if not safety.wasSuccessful():
        report_path.write_text(json.dumps({'status': 'FAIL', 'reason': 'NATIVE_SAFETY_TESTS_FAILED'}), encoding='utf-8')
        print('Native safety tests failed; validation stopped before model analysis.')
        return 1
    lab = ROOT / 'experimental_lab/tolerance_extraction_audit'
    dataset = BenchmarkDataset.load(lab / 'benchmarks/datasets/silver_verified_cases_snapshot.json')
    snapshot = FeatureCaseBase(str(lab / 'benchmarks/snapshots/feature_case_base_snapshot.json'))
    rows = []
    for record in dataset.records:
        base = FeatureCaseBase.__new__(FeatureCaseBase)
        base.cases = [case for case in snapshot.cases if case.is_retrieval_eligible() and _case_group(case) != record.group_id]
        assert all(_case_group(case) != record.group_id for case in base.cases)
        q = record.query
        node = _record_node(record)
        nominal, kind = float(q['nominal_value']), q['dimension_category']
        old = ToleranceDecisionService(base)._evaluate_recommendation(
            record.benchmark_id, ToleranceBenchmarkRunner._service_category(record), kind == 'DIAMETER', nominal,
            node, q['part_type'], q.get('product_family'), dimension_category=kind).to_dict()
        new = NativeToleranceEngine(base).recommend_measurement(
            node, record.benchmark_id, nominal, kind, q.get('inferred_role') or '', q['part_type'], q.get('product_family'))
        rows.append({'id': record.benchmark_id, 'baseline_correct': tolerance_equal(old['tolerance_config'], record.expected['tolerance_config']),
                     'experimental_correct': tolerance_equal(new['tolerance_config'], record.expected['tolerance_config']),
                     'baseline_accepted': old['decision_status'] != 'REVIEW_REQUIRED',
                     'experimental_accepted': new['decision_status'] != 'REVIEW_REQUIRED'})
    metrics = {}
    for name in ['baseline', 'experimental']:
        accepted = [row for row in rows if row[name + '_accepted']]
        metrics[name] = {'sample_count': len(rows),
            'exact_accuracy': sum(row[name + '_correct'] for row in rows) / len(rows),
            'coverage': len(accepted) / len(rows),
            'selective_accuracy': sum(row[name + '_correct'] for row in accepted) / len(accepted) if accepted else None}
    gates = {'exact_accuracy_not_lower': metrics['experimental']['exact_accuracy'] >= metrics['baseline']['exact_accuracy'],
             'native_safety_tests_passed': safety.wasSuccessful(),
             'coverage_not_lower': metrics['experimental']['coverage'] >= metrics['baseline']['coverage'],
             'selective_accuracy_not_lower': metrics['experimental']['selective_accuracy'] == metrics['baseline']['selective_accuracy']
                 or (metrics['experimental']['selective_accuracy'] or 0) > (metrics['baseline']['selective_accuracy'] or 0)}
    # Positive controls are essential: two engines abstaining on every held-out
    # query are not evidence of precision. Replay is labelled separately and
    # never presented as unseen-part generalization accuracy.
    replay_correct, replay_accepted, baseline_replay_correct = 0, 0, 0
    eligible = FeatureCaseBase.__new__(FeatureCaseBase)
    eligible.cases = [case for case in snapshot.cases if case.is_retrieval_eligible()]
    for record in dataset.records:
        q = record.query
        reference = ToleranceDecisionService(eligible)._evaluate_recommendation(
            record.benchmark_id, ToleranceBenchmarkRunner._service_category(record), q['dimension_category'] == 'DIAMETER',
            float(q['nominal_value']), _record_node(record), q['part_type'], q.get('product_family'),
            dimension_category=q['dimension_category']).to_dict()
        baseline_replay_correct += tolerance_equal(reference['tolerance_config'], record.expected['tolerance_config'])
        rec = NativeToleranceEngine(eligible).recommend_measurement(
            _record_node(record), record.benchmark_id, float(q['nominal_value']), q['dimension_category'],
            q.get('inferred_role') or '', q['part_type'], q.get('product_family'))
        replay_correct += tolerance_equal(rec['tolerance_config'], record.expected['tolerance_config'])
        replay_accepted += rec['decision_status'] != 'REVIEW_REQUIRED'
    gates['positive_evidence_replay_exact'] = replay_correct == len(dataset.records)
    gates['positive_evidence_replay_not_lower'] = replay_correct >= baseline_replay_correct
    gates['positive_evidence_replay_coverage'] = replay_accepted == len(dataset.records)
    models = []
    paths = [
        ROOT / 'auto_2d_drawing/output/BLADE_ASSY-1AC085000H-R01_37f6697b_batch/_parts/SHAFT-2AC085010H_1.stp',
        ROOT / 'auto_2d_drawing/output/BLADE_ASSY-1AC085000H-R01_37f6697b_batch/_parts/MOTOR_CASING-2AC084010H_1.stp',
        ROOT / 'models/BLADE_ASSY-1AC085000H-R01.stp',
    ]
    for path in paths:
        result = recommend_step(path)
        recs = list(result['recommendations'].values())
        models.append({'model': path.name, 'rules': len(recs), 'native_measurements': len(result['native_measurements']),
                       'tiers': dict(Counter(item['tier_level'] for item in recs)),
                       'blocked': sum(item['tier_level'] == 'NOT_APPLICABLE' for item in recs)})
        assert recs, 'real model produced no candidates'
        assert all(item['recommended_mode'] == 'NONE' for item in recs if item['tier_level'] == 'NOT_APPLICABLE')
    report = {'engine': ENGINE_VERSION, 'status': 'PASS_FOR_ADMIN_TEST_ONLY' if all(gates.values()) else 'FAIL',
              'engine_sha256': hashlib.sha256((Path(__file__).parent / 'engine.py').read_bytes()).hexdigest(),
              'validated_sources': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in [
                  'auto_2d_drawing/tolerance/tolerance_decision_service.py',
                  'auto_2d_drawing/tolerance/case_base.py',
                  'auto_2d_drawing/tolerance/iso_tolerance_table.py',
                  'auto_2d_drawing/smart_extractors/experimental_candidate_adapter.py',
              ]},
              'protocol': 'LEAVE_ONE_PART_GROUP_OUT; same frozen SILVER snapshot; policy-stage comparison, not end-to-end GOLD accuracy',
              'metrics': metrics, 'gates': gates, 'real_models': models,
              'positive_evidence_replay': {'sample_count': len(dataset.records), 'baseline_exact_correct': baseline_replay_correct, 'exact_correct': replay_correct, 'accepted': replay_accepted},
              'native_safety_tests': safety.testsRun,
              'limitations': ['20 SILVER cases primarily shaft/hole; native relation/angle labels lack signed GOLD.',
                             'Held-out current policy accepts zero cases; generalization precision cannot be established.',
                             'Real-model native extraction coverage is reported separately from fixed-query policy accuracy.'],
              'rows': rows}
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'rows'}))
    return 0 if all(gates.values()) else 1


if __name__ == '__main__':
    raise SystemExit(run())
