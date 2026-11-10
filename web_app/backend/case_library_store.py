"""Private collections and reviewed publication; payloads remain ToleranceCase[]."""
import copy
import json
import threading
import uuid
from datetime import datetime, timezone
from sqlalchemy import select, insert, update, delete, and_, or_
from web_app.backend.identity_store import case_library_tables as T, annotation_artifacts, artifact_tag_assignments, recommendation_tags
from auto_2d_drawing.tolerance.case_base import ToleranceCase


def now():
    return datetime.now(timezone.utc)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class CaseLibraryStore:
    def __init__(self, identity):
        self.identity, self.engine = identity, identity.engine
        self.lock = threading.RLock()

    @staticmethod
    def _name(value):
        value = str(value or '').strip()
        if not value or len(value) > 160:
            raise ValueError('名稱必須是 1–160 個字元')
        return value

    @staticmethod
    def _owned(conn, table, record_id, owner, column='id'):
        row = conn.execute(select(table).where(and_(table.c[column] == record_id, table.c.owner_user_id == owner))).mappings().first()
        if not row:
            raise KeyError('資料不存在或不屬於目前帳號')
        return dict(row)

    def customers(self, owner):
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(select(T.customers).where(T.customers.c.owner_user_id == owner).order_by(T.customers.c.name)).mappings()]

    def create_customer(self, owner, name):
        value = {'id': str(uuid.uuid4()), 'owner_user_id': owner, 'name': self._name(name), 'created_at': now()}
        with self.engine.begin() as conn:
            conn.execute(insert(T.customers).values(**value))
        return value

    def collections(self, owner):
        with self.engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(select(T.collections).where(and_(T.collections.c.owner_user_id == owner, T.collections.c.is_active.is_(True))).order_by(T.collections.c.name)).mappings()]
            memberships = conn.execute(select(T.memberships.c.collection_id, T.memberships.c.case_record_id).select_from(T.memberships.join(T.payloads)).where(and_(T.payloads.c.owner_user_id == owner, T.payloads.c.is_active.is_(True)))).all()
        counts = {}
        for collection, case in memberships:
            counts.setdefault(collection, set()).add(case)
        for row in rows:
            row['case_count'] = len(counts.get(row['id'], set()))
        return rows

    def create_collection(self, owner, payload):
        private, global_tag = payload.get('private_customer_id') or None, payload.get('customer_tag_id') or None
        if private and global_tag:
            raise ValueError('請選私人客戶或全局客戶其中一種')
        value = {'id': str(uuid.uuid4()), 'owner_user_id': owner, 'name': self._name(payload.get('name')),
                 'description': str(payload.get('description') or '')[:1000], 'private_customer_id': private,
                 'customer_tag_id': global_tag, 'is_active': True, 'created_at': now(), 'updated_at': now()}
        with self.engine.begin() as conn:
            if private:
                self._owned(conn, T.customers, private, owner)
            if global_tag:
                row = conn.execute(select(recommendation_tags).where(and_(recommendation_tags.c.id == global_tag,
                    recommendation_tags.c.dimension == 'CUSTOMER', recommendation_tags.c.is_active.is_(True)))).first()
                if not row:
                    raise ValueError('全局客戶標籤不存在或已停用')
            conn.execute(insert(T.collections).values(**value))
        return value

    def update_collection(self, owner, collection_id, payload):
        values = {'updated_at': now()}
        if 'name' in payload: values['name'] = self._name(payload['name'])
        if 'description' in payload: values['description'] = str(payload['description'])[:1000]
        if 'is_active' in payload: values['is_active'] = bool(payload['is_active'])
        with self.engine.begin() as conn:
            self._owned(conn, T.collections, collection_id, owner)
            conn.execute(update(T.collections).where(T.collections.c.id == collection_id).values(**values))
        return {'id': collection_id, **values}

    def artifact(self, owner, artifact_id):
        with self.engine.connect() as conn:
            row = self._owned(conn, annotation_artifacts, artifact_id, owner)
        row['input_snapshot'] = json.loads(row.pop('input_snapshot_json'))
        row['output_files'] = json.loads(row.pop('output_files_json'))
        return row

    def artifacts(self, owner):
        with self.engine.connect() as conn:
            rows = conn.execute(select(annotation_artifacts).where(annotation_artifacts.c.owner_user_id == owner).order_by(annotation_artifacts.c.created_at.desc()).limit(100)).mappings().all()
        result = []
        for row in rows:
            value = dict(row)
            snapshot = json.loads(value.pop('input_snapshot_json'))
            value['output_files'] = json.loads(value.pop('output_files_json'))
            source = snapshot.get('source_metadata') or {}
            value['candidate_engine'] = source.get('candidate_engine')
            value['can_curate'] = source.get('candidate_engine') == 'experimental_v026' and bool(source.get('step_sha256'))
            value['features'] = [{key: record.get(key) for key in ('rule_id','id','name','enabled','nominal_value','measurement_role','tolerance','tolerance_config')}
                                 for record in snapshot.get('feature_records') or [] if record.get('enabled')]
            result.append(value)
        return result

    def add_cases(self, owner, artifact_id, collection_ids, payloads):
        collection_ids = list(dict.fromkeys(collection_ids))
        if not collection_ids or not payloads:
            raise ValueError('請選擇案例集與至少一個已驗證尺寸')
        result = []
        with self.lock, self.engine.begin() as conn:
            self._owned(conn, annotation_artifacts, artifact_id, owner)
            for cid in collection_ids:
                collection = self._owned(conn, T.collections, cid, owner)
                if not collection['is_active']: raise ValueError('案例集已封存')
            for payload in payloads:
                case = ToleranceCase(**copy.deepcopy(payload))
                meta = case.source_metadata
                if meta.get('owner_user_id') != owner or meta.get('source_artifact_id') != artifact_id or meta.get('case_contract') != 'NATIVE_MEASUREMENT_V1' or not case.is_verified_extraction():
                    raise ValueError('案例來源或新版幾何驗證不一致')
                existing = conn.execute(select(T.payloads.c.case_record_id).where(and_(T.payloads.c.owner_user_id == owner, T.payloads.c.content_hash == meta['content_hash']))).scalar_one_or_none()
                record_id = existing or str(uuid.uuid4())
                if not existing:
                    conn.execute(insert(T.payloads).values(case_record_id=record_id, owner_user_id=owner,
                        artifact_id=artifact_id, case_id=case.case_id, content_hash=meta['content_hash'], case_json=encode(case.to_dict()), is_active=True, created_at=now()))
                else:
                    conn.execute(update(T.payloads).where(T.payloads.c.case_record_id == record_id).values(is_active=True))
                for cid in collection_ids:
                    if not conn.execute(select(T.memberships).where(and_(T.memberships.c.collection_id == cid, T.memberships.c.case_record_id == record_id))).first():
                        conn.execute(insert(T.memberships).values(collection_id=cid, case_record_id=record_id))
                result.append(record_id)
        return {'case_record_ids': result, 'case_count': len(result), 'collection_ids': collection_ids}

    def cases(self, owner, collection_ids=None, tag_ids=None, match_mode='ANY', limit=None, offset=0):
        wanted = set(collection_ids or [])
        with self.engine.connect() as conn:
            for cid in wanted:
                self._owned(conn, T.collections, cid, owner)
            linked = select(T.memberships.c.case_record_id).join(T.collections).where(and_(
                T.collections.c.owner_user_id == owner, T.collections.c.is_active.is_(True),
                T.memberships.c.case_record_id == T.payloads.c.case_record_id))
            query = select(T.payloads).where(and_(T.payloads.c.owner_user_id == owner,
                T.payloads.c.is_active.is_(True), linked.exists()))
            if wanted:
                query = query.where(linked.where(T.collections.c.id.in_(wanted)).exists())
            tag_conditions = []
            for tag in set(tag_ids or []):
                tag_conditions.append(or_(linked.where(T.collections.c.customer_tag_id == tag).exists(),
                    select(artifact_tag_assignments.c.tag_id).where(and_(
                        artifact_tag_assignments.c.artifact_id == T.payloads.c.artifact_id,
                        artifact_tag_assignments.c.tag_id == tag)).exists()))
            if tag_conditions:
                query = query.where((and_ if match_mode == 'ALL' else or_)(*tag_conditions))
            query = query.order_by(T.payloads.c.created_at.desc(), T.payloads.c.case_record_id).offset(offset)
            if limit is not None: query = query.limit(limit)
            rows = conn.execute(query).mappings().all()
            memberships = conn.execute(select(T.memberships, T.collections.c.customer_tag_id).select_from(T.memberships.join(T.collections)).where(and_(T.collections.c.owner_user_id == owner, T.collections.c.is_active.is_(True),
                T.memberships.c.case_record_id.in_([row['case_record_id'] for row in rows])))).mappings().all()
            artifact_tags = conn.execute(select(artifact_tag_assignments).select_from(artifact_tag_assignments.join(annotation_artifacts)).where(and_(annotation_artifacts.c.owner_user_id == owner,
                artifact_tag_assignments.c.artifact_id.in_([row['artifact_id'] for row in rows])))).mappings().all()
        links, tags = {}, {}
        for row in memberships:
            links.setdefault(row['case_record_id'], set()).add(row['collection_id'])
            if row['customer_tag_id']: tags.setdefault(row['case_record_id'], set()).add(row['customer_tag_id'])
        artifact_tag_map = {}
        for row in artifact_tags: artifact_tag_map.setdefault(row['artifact_id'], set()).add(row['tag_id'])
        result = []
        selected = set(tag_ids or [])
        for row in rows:
            linked = links.get(row['case_record_id'], set())
            if not linked or (wanted and not wanted.intersection(linked)): continue
            assigned = tags.get(row['case_record_id'], set()) | artifact_tag_map.get(row['artifact_id'], set())
            if selected and not (selected.issubset(assigned) if match_mode == 'ALL' else bool(selected & assigned)): continue
            payload = json.loads(row['case_json'])
            result.append({'id': row['case_record_id'], 'case': payload, 'collection_ids': sorted(linked),
                           'tag_ids': sorted(assigned), 'created_at': row['created_at']})
        return result

    def remove_case(self, owner, record_id):
        with self.engine.begin() as conn:
            self._owned(conn, T.payloads, record_id, owner, 'case_record_id')
            conn.execute(update(T.payloads).where(T.payloads.c.case_record_id == record_id).values(is_active=False))
            conn.execute(delete(T.memberships).where(T.memberships.c.case_record_id == record_id))

    def request_share(self, owner, record_id):
        with self.lock, self.engine.begin() as conn:
            case = self._owned(conn, T.payloads, record_id, owner, 'case_record_id')
            if not case['is_active']: raise ValueError('案例已移除')
            existing = conn.execute(select(T.shares).where(T.shares.c.case_record_id == record_id)).mappings().first()
            if existing:
                if existing['status'] == 'REJECTED':
                    conn.execute(update(T.shares).where(T.shares.c.id == existing['id']).values(
                        status='PENDING', created_at=now(), reviewed_at=None, reviewed_by=None))
                    return {**dict(existing), 'status': 'PENDING'}
                return dict(existing)
            value = {'id': str(uuid.uuid4()), 'case_record_id': record_id, 'owner_user_id': owner,
                     'status': 'PENDING', 'created_at': now()}
            conn.execute(insert(T.shares).values(**value))
            return value

    def share_requests(self):
        with self.engine.connect() as conn:
            rows = conn.execute(select(T.shares, T.payloads.c.case_json).select_from(T.shares.join(T.payloads)).where(T.shares.c.status == 'PENDING')).mappings().all()
        return [{**{key: value for key, value in row.items() if key != 'case_json'}, 'case': json.loads(row['case_json'])} for row in rows]

    def review_share(self, admin, request_id, approve, tag_ids):
        if not admin.is_admin:
            raise PermissionError('需要管理員核准')
        tags = self.identity.validate_recommendation_tag_ids(tag_ids)
        with self.lock, self.engine.begin() as conn:
            row = conn.execute(select(T.shares).where(T.shares.c.id == request_id)).mappings().first()
            if not row: raise KeyError('申請不存在')
            if row['status'] != 'PENDING': raise ValueError('此申請已審核')
            case_row = conn.execute(select(T.payloads).where(T.payloads.c.case_record_id == row['case_record_id'])).mappings().one()
            if approve and not case_row['is_active']: raise ValueError('原案例已移除，不能發布')
            if approve:
                payload = json.loads(case_row['case_json'])
                payload['case_id'] = 'SHARED_' + payload['case_id'].removeprefix('NATIVE_')
                payload['source_metadata'].update(scope='COMPANY', approved_by=admin.id, approved_at=now().isoformat())
                conn.execute(insert(T.published).values(case_id=payload['case_id'], origin_case_record_id=row['case_record_id'],
                    case_json=encode(ToleranceCase(**payload).to_dict()), tag_ids_json=encode(tags), published_at=now()))
            conn.execute(update(T.shares).where(T.shares.c.id == request_id).values(status='APPROVED' if approve else 'REJECTED', reviewed_by=admin.id, reviewed_at=now()))
        return {'status': 'APPROVED' if approve else 'REJECTED'}

    def published(self, tag_ids=None, match_mode='ANY'):
        with self.engine.connect() as conn:
            rows = conn.execute(select(T.published)).mappings().all()
        selected = set(tag_ids or [])
        return [json.loads(row['case_json']) for row in rows if not selected or
                (selected.issubset(set(json.loads(row['tag_ids_json']))) if match_mode == 'ALL' else bool(selected & set(json.loads(row['tag_ids_json']))))]

    def case_for_source(self, user, case_id):
        with self.engine.connect() as conn:
            row = conn.execute(select(T.published).where(T.published.c.case_id == case_id)).mappings().first()
            if row: return json.loads(row['case_json'])
            row = conn.execute(select(T.payloads).where(and_(T.payloads.c.case_id == case_id, T.payloads.c.owner_user_id == user.id))).mappings().first()
            if row: return json.loads(row['case_json'])
            if user.is_admin:
                row = conn.execute(select(T.payloads.c.case_json).select_from(T.payloads.join(T.shares)).where(and_(T.payloads.c.case_id == case_id, T.shares.c.status == 'PENDING'))).scalar_one_or_none()
                if row: return json.loads(row)
        raise KeyError('案例不存在或無權限')
