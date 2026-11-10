import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from web_app.backend.identity_store import IdentityStore
from web_app.backend.case_library_store import CaseLibraryStore
from auto_2d_drawing.tolerance.case_base import ToleranceCase, FeatureCaseBase
from auto_2d_drawing.tolerance.feature_graph import FeatureNode
from auto_2d_drawing.tolerance.native_case_contract import build_native_cases, historical_compatibility, merge_compatible_cases
from experimental_lab.tolerance_extraction_audit.TOLERANCE_V1_EXPERIMENT.test_engine import fixture
from experimental_lab.tolerance_extraction_audit.TOLERANCE_V1_EXPERIMENT.engine import NativeToleranceEngine


class CaseLibraryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = IdentityStore('sqlite:///' + (Path(self.directory.name) / 'identity.db').as_posix())
        self.addCleanup(self.store.close)
        self.admin = self.store.authenticate('admin', 'ForceconAdmin!2026')
        for name in ['one', 'two']:
            self.store.create_user({'username': name, 'display_name': name, 'email': name + '@forcecon.com.tw',
                'role': 'ENGINEER', 'password': 'Case-Test-Password-2026'}, self.admin.id)
        self.one = self.store.authenticate('one', 'Case-Test-Password-2026')
        self.two = self.store.authenticate('two', 'Case-Test-Password-2026')
        self.library = CaseLibraryStore(self.store)
        self.record = {'rule_id': 'native-overall', 'enabled': True, 'source_engine': 'experimental_v026',
                       'tolerance_config': {'mode': 'CUSTOM_SYMMETRIC', 'dev': .1}, 'views': ['right'], 'side': 'TOP'}
        self.artifact = self.store.record_artifact_and_cases(self.one, 'model', 'shaft', [self.record], {'pdf_url': '/api/files/model/drawing.pdf'},
            learn_cases=False, source_metadata={'candidate_engine': 'experimental_v026', 'step_sha256': 'a' * 64})['artifact_id']
        self.customer = self.library.create_customer(self.one.id, 'Private customer')
        self.collection = self.library.create_collection(self.one.id, {'name': 'Project A', 'private_customer_id': self.customer['id']})
        self.payloads = build_native_cases(fixture(), [self.record], owner_id=self.one.id, source_sha256='a' * 64,
                                          artifact_id=self.artifact, model_id='model', part_id='shaft')

    def add(self, collections=None, payloads=None):
        return self.library.add_cases(self.one.id, self.artifact, collections or [self.collection['id']], payloads or self.payloads)

    def test_artifact_is_automatic_but_recommendation_case_requires_explicit_add(self):
        self.assertEqual(self.store.personal_cases(self.one.id), [])
        self.assertEqual(self.library.cases(self.one.id), [])
        self.assertEqual(len(self.library.artifacts(self.one.id)), 1)
        self.add()
        self.assertEqual(len(self.library.cases(self.one.id)), 1)

    def test_private_customer_and_collection_are_not_shared(self):
        self.assertEqual(self.library.customers(self.two.id), [])
        self.assertEqual(self.library.collections(self.two.id), [])
        self.assertFalse(any(tag['name'] == 'Private customer' for tag in self.store.list_recommendation_tags()))
        with self.assertRaises(KeyError):
            self.library.create_collection(self.two.id, {'name': 'Forbidden', 'private_customer_id': self.customer['id']})
        with self.assertRaises(KeyError):
            self.library.cases(self.two.id, [self.collection['id']])

    def test_customer_rename_keeps_links_and_owner_isolation(self):
        renamed = self.library.update_customer(self.one.id, self.customer['id'], 'Renamed customer')
        self.assertEqual(renamed['name'], 'Renamed customer')
        self.assertEqual(self.library.collections(self.one.id)[0]['private_customer_id'], self.customer['id'])
        for action in (lambda: self.library.update_customer(self.two.id, self.customer['id'], 'Forbidden'),
                       lambda: self.library.delete_customer(self.two.id, self.customer['id'])):
            with self.assertRaises(KeyError): action()
        self.assertEqual(self.library.customers(self.one.id)[0]['name'], 'Renamed customer')

    def test_customer_delete_detaches_active_and_archived_collections_but_keeps_cases(self):
        self.add()
        archived = self.library.create_collection(self.one.id, {'name': 'Archived', 'private_customer_id': self.customer['id']})
        self.library.update_collection(self.one.id, archived['id'], {'is_active': False})
        before = self.library.cases(self.one.id)[0]['case']
        result = self.library.delete_customer(self.one.id, self.customer['id'])
        self.assertEqual(result['detached_collection_count'], 2)
        self.assertEqual(self.library.customers(self.one.id), [])
        self.assertIsNone(self.library.collections(self.one.id)[0]['private_customer_id'])
        self.library.update_collection(self.one.id, archived['id'], {'is_active': True})
        self.assertTrue(all(row['private_customer_id'] is None for row in self.library.collections(self.one.id)))
        self.assertEqual(self.library.cases(self.one.id)[0]['case'], before)
        self.assertEqual(self.library.artifact(self.one.id, self.artifact)['id'], self.artifact)
        with self.assertRaises(KeyError): self.library.delete_customer(self.one.id, self.customer['id'])

    def test_customer_scope_does_not_expand_empty_customers_or_cross_collection_memberships(self):
        self.add()
        empty = self.library.create_customer(self.one.id, 'Empty customer')
        other = self.library.create_collection(self.one.id, {'name': 'Other customer project', 'private_customer_id': empty['id']})
        self.assertEqual(len(self.library.cases(self.one.id, customer_id=self.customer['id'])), 1)
        self.assertEqual(self.library.cases(self.one.id, customer_id=empty['id']), [])
        with self.assertRaises(KeyError): self.library.cases(self.two.id, customer_id=self.customer['id'])
        self.add([other['id']])
        self.assertEqual(self.library.cases(self.one.id, [other['id']], customer_id=self.customer['id']), [])
        self.assertEqual(len(self.library.cases(self.one.id, [other['id']], customer_id=empty['id'])), 1)

    def test_customer_crud_http_api_validates_names_duplicates_and_ownership(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from web_app.backend import case_library_api as api
        app = FastAPI(); app.include_router(api.router)
        app.dependency_overrides[api.require_user] = lambda: self.one
        with patch.object(api, 'library', self.library), TestClient(app) as client:
            customer = client.post('/api/case-library/customers', json={'name': 'API client'})
            self.assertEqual(customer.status_code, 201)
            path = '/api/case-library/customers/' + customer.json()['id']
            self.assertEqual(client.patch(path, json={'name': 'Updated'}).json()['name'], 'Updated')
            for name in ('', ' ' * 3, 'x' * 161, self.customer['name']):
                self.assertEqual(client.patch(path, json={'name': name}).status_code, 409)
            app.dependency_overrides[api.require_user] = lambda: self.two
            self.assertEqual(client.patch(path, json={'name': 'Not mine'}).status_code, 404)
            self.assertEqual(client.delete(path).status_code, 404)
            app.dependency_overrides[api.require_user] = lambda: self.one
            self.assertEqual(client.delete(path).status_code, 200)
            self.assertEqual(client.delete(path).status_code, 404)

    def test_repeated_add_and_multiple_collections_reference_one_case(self):
        second = self.library.create_collection(self.one.id, {'name': 'Project B'})
        first = self.add()
        again = self.add([second['id']])
        self.assertEqual(first['case_record_ids'], again['case_record_ids'])
        rows = self.library.cases(self.one.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]['collection_ids']), {self.collection['id'], second['id']})
        base = FeatureCaseBase.__new__(FeatureCaseBase)
        base.cases = []
        merged = merge_compatible_cases([], [rows[0]['case'], rows[0]['case']])
        self.assertEqual(len(merged.cases), 1)

    def test_export_round_trips_through_historical_case_loader(self):
        self.add()
        payload = self.library.cases(self.one.id)[0]['case']
        self.assertEqual(set(payload), set(ToleranceCase.__dataclass_fields__))
        path = Path(self.directory.name) / 'compatible.json'
        path.write_text(json.dumps([payload]), encoding='utf-8')
        loaded = FeatureCaseBase(str(path))
        self.assertEqual(len(loaded.cases), 1)
        self.assertTrue(loaded.cases[0].is_verified_extraction())
        self.assertEqual(loaded.cases[0].source_metadata['extraction_engine'], fixture()['method'])

    def test_legacy_evidence_is_assessed_without_mutating_original(self):
        legacy = ToleranceCase(**copy.deepcopy(self.payloads[0]))
        legacy.case_id = 'HIST_LEGACY'
        legacy.source_metadata = {'feature_identity_verified': True, 'geometry_verification': {'passed': True}, 'dimension_category': 'LINEAR'}
        before = copy.deepcopy(legacy.to_dict())
        self.assertTrue(historical_compatibility(legacy).is_retrieval_eligible())
        legacy.source_metadata.pop('dimension_category')
        adapted = historical_compatibility(legacy)
        self.assertFalse(adapted.is_retrieval_eligible())
        self.assertEqual(legacy.verification_status, 'ENGINEER_VERIFIED')
        self.assertEqual(before['nominal_dimensions'], legacy.nominal_dimensions)

    def test_shared_publication_requires_review_and_deduplicates_personal_origin(self):
        record = self.add()['case_record_ids'][0]
        with self.assertRaises(KeyError): self.library.case_for_source(self.two, self.payloads[0]['case_id'])
        request = self.library.request_share(self.one.id, record)
        self.assertEqual(self.library.published(), [])
        with self.assertRaises(PermissionError): self.library.review_share(self.two, request['id'], True, [])
        self.library.review_share(self.admin, request['id'], True, [])
        published = self.library.published()
        self.assertEqual(len(published), 1)
        self.assertEqual(len(merge_compatible_cases([], published + self.payloads).cases), 1)
        self.assertEqual(self.library.case_for_source(self.two, published[0]['case_id'])['source_metadata']['scope'], 'COMPANY')
        self.assertNotIn('Private customer', json.dumps(published))
        self.assertEqual(self.library.cases(self.two.id), [])

    def test_conflicting_native_versions_require_engineer_choice(self):
        second = copy.deepcopy(self.record)
        second['tolerance_config']['dev'] = .2
        other = build_native_cases(fixture(), [second], owner_id=self.one.id, source_sha256='a' * 64,
                                  artifact_id=self.artifact, model_id='model', part_id='shaft')
        self.assertNotEqual(other[0]['case_id'], self.payloads[0]['case_id'])
        report = fixture(); report['source_step_sha256'] = 'a' * 64
        result = NativeToleranceEngine(merge_compatible_cases([], self.payloads + other)).recommend_report(report)
        rec = result['recommendations']['native-overall']
        self.assertEqual(rec['recommended_mode'], 'NONE')
        self.assertEqual(rec['decision_status'], 'REVIEW_REQUIRED')
        self.assertEqual(len(rec['alternatives']), 2)

    def test_exact_private_case_can_be_recommended_without_personal_weights(self):
        report = fixture(); report['source_step_sha256'] = 'a' * 64
        result = NativeToleranceEngine(merge_compatible_cases([], self.payloads)).recommend_report(report)
        rec = result['recommendations']['native-overall']
        self.assertEqual(rec['tolerance_config'], {'mode': 'CUSTOM_SYMMETRIC', 'dev': .1})
        self.assertEqual(rec['confidence_basis'], 'EXACT_NATIVE_SOURCE_IDENTITY')

    def test_historical_family_uses_drawing_number_despite_batch_label_metadata(self):
        payload = copy.deepcopy(self.payloads[0])
        payload['source_metadata'].pop('case_contract')
        payload['source_metadata'].update(product_family='UNRELATED_BATCH_LABEL', drawing_file='2AL0W5010H-R01.dxf')
        base = merge_compatible_cases([ToleranceCase(**payload)], [])
        node = FeatureNode(id='query', feature_type=payload['feature_type'], nominal=payload['nominal_dimensions'],
            inferred_role=payload['inferred_role'], axial_span=(0, 1), center_axial=.5, source_info={})
        match = base.search_similar_cases_detailed(node, part_type='GENERAL', product_family='AL0W', dimension_category='LINEAR')[0]
        self.assertEqual(match['product_family'], 'AL0W')
        self.assertTrue(match['same_product_family'])
        self.assertEqual(match['score_breakdown']['product_family'], 1.0)

    def test_native_family_uses_explicit_contract_without_drawing_number(self):
        payload = copy.deepcopy(self.payloads[0])
        payload['source_metadata'].update(product_family='AL0W', drawing_file='NATIVE_hash.dxf')
        base = merge_compatible_cases([], [payload])
        node = FeatureNode(id='query', feature_type=payload['feature_type'], nominal=payload['nominal_dimensions'],
            inferred_role=payload['inferred_role'], axial_span=(0, 1), center_axial=.5, source_info={})
        match = base.search_similar_cases_detailed(node, part_type='GENERAL', product_family='AL0W', dimension_category='LINEAR')[0]
        self.assertTrue(match['same_product_family'])

    def test_id_collision_stops_merge_and_unverified_native_geometry_is_rejected(self):
        company = ToleranceCase(**self.payloads[0]); company.tolerance_config = {'mode': 'CUSTOM_SYMMETRIC', 'dev': .8}
        with self.assertRaises(ValueError): merge_compatible_cases([company], self.payloads)
        report = fixture(); report['dimension_intents']['intents'][0]['verification']['checks'][0]['passed'] = False
        with self.assertRaises(ValueError): build_native_cases(report, [self.record], owner_id=self.one.id,
            source_sha256='a' * 64, artifact_id=self.artifact, model_id='model', part_id='shaft')

    def test_removing_case_retains_original_evidence(self):
        record = self.add()['case_record_ids'][0]
        self.library.remove_case(self.one.id, record)
        self.assertEqual(self.library.cases(self.one.id), [])
        self.assertEqual(self.library.artifact(self.one.id, self.artifact)['id'], self.artifact)
        with self.assertRaises(ValueError): self.store.delete_artifact(self.one, self.artifact)

    def test_native_case_evidence_prevents_destructive_account_deletion(self):
        self.add()
        with self.assertRaisesRegex(ValueError, '停用帳號'):
            self.store.delete_users([self.one.id], self.admin.id)

    def test_global_customer_tag_filters_private_cases_without_sharing_them(self):
        tag = self.store.create_recommendation_tag({'dimension': 'CUSTOMER', 'key': 'client-a', 'name': 'Client A'}, self.admin.id)
        collection = self.library.create_collection(self.one.id, {'name': 'Global client project', 'customer_tag_id': tag['id']})
        self.add([collection['id']])
        self.assertEqual(len(self.library.cases(self.one.id, tag_ids=[tag['id']])), 1)
        self.assertEqual(len(self.library.cases(self.one.id, tag_ids=[tag['id'], 'missing'], match_mode='ANY')), 1)
        self.assertEqual(self.library.cases(self.one.id, tag_ids=[tag['id'], 'missing'], match_mode='ALL'), [])
        self.assertEqual(self.library.cases(self.two.id, tag_ids=[tag['id']]), [])
        self.library.update_collection(self.one.id, collection['id'], {'is_active': False})
        self.assertEqual(self.library.cases(self.one.id, tag_ids=[tag['id']]), [])

    def test_scoped_pagination_filters_before_loading_payloads(self):
        self.add()
        other = self.library.create_collection(self.one.id, {'name': 'Empty'})
        self.assertEqual(self.library.cases(self.one.id, [other['id']], limit=1), [])
        self.assertEqual(len(self.library.cases(self.one.id, limit=1)), 1)
        self.assertEqual(self.library.cases(self.one.id, limit=1, offset=1), [])
        self.assertEqual(self.library.cases(self.one.id, tag_ids=['missing'], limit=1), [])

    def test_curation_api_rejects_unconfirmed_forged_and_other_owner_selections(self):
        from fastapi import HTTPException
        from web_app.backend import case_library_api as api, server
        with patch.object(api, 'library', self.library), patch.object(api, 'assert_model_access'), \
                patch.object(server, '_run_cad_annotation_worker') as worker:
            for owner, body, status in [
                (self.one, {'confirmed': False}, 400),
                (self.two, {'confirmed': True}, 404),
                (self.one, {'confirmed': True, 'rule_ids': ['forged'], 'collection_ids': [self.collection['id']]}, 400),
            ]:
                with self.assertRaises(HTTPException) as caught:
                    api.curate_artifact(self.artifact, body, owner)
                self.assertEqual(caught.exception.status_code, status)
            worker.assert_not_called()

    def test_api_uses_saved_tolerances_not_client_supplied_payload(self):
        from web_app.backend import case_library_api as api, server
        part_dir = Path(self.directory.name) / '_parts'; part_dir.mkdir()
        (part_dir / 'shaft.stp').write_text('fixture')
        with patch.object(api, 'library', self.library), patch.object(api, 'assert_model_access'), \
                patch.object(server, '_safe_output_dir', return_value=self.directory.name), \
                patch.object(server, '_run_cad_annotation_worker', return_value={'cases': self.payloads}) as worker:
            api.curate_artifact(self.artifact, {'confirmed': True, 'rule_ids': ['native-overall'],
                'collection_ids': [self.collection['id']], 'tolerance_config': {'mode': 'CUSTOM_SYMMETRIC', 'dev': 99}}, self.one)
            self.assertEqual(worker.call_args.args[0]['records'][0]['tolerance_config']['dev'], .1)
            exported = json.loads(api.export_cases([], self.one).body)
            self.assertEqual(exported[0]['tolerance_config']['dev'], .1)
