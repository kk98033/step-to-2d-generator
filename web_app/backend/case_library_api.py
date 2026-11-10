"""Explicit case curation and collection APIs; all private queries are owner-bound."""
import json
from collections import Counter
from pathlib import Path
from urllib.parse import unquote
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import Response, FileResponse
from sqlalchemy.exc import IntegrityError
from web_app.backend.account_api import store, require_user, require_admin, assert_model_access
from web_app.backend.identity_store import CurrentUser
from web_app.backend.case_library_store import CaseLibraryStore
from auto_2d_drawing.config import OUTPUT_DIR

router = APIRouter(prefix='/api/case-library', tags=['case-library'])
library = CaseLibraryStore(store)


def call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except KeyError as error:
        raise HTTPException(404, detail=str(error)) from error
    except (ValueError, IntegrityError) as error:
        raise HTTPException(409, detail=str(error) if isinstance(error, ValueError) else '資料重複，請重新載入') from error


@router.get('/collections')
def collections(user: CurrentUser = Depends(require_user)):
    return {'collections': library.collections(user.id), 'customers': library.customers(user.id)}


@router.post('/customers', status_code=201)
def create_customer(body: dict = Body(...), user: CurrentUser = Depends(require_user)):
    return call(library.create_customer, user.id, body.get('name'))


@router.post('/collections', status_code=201)
def create_collection(body: dict = Body(...), user: CurrentUser = Depends(require_user)):
    return call(library.create_collection, user.id, body)


@router.patch('/collections/{collection_id}')
def update_collection(collection_id: str, body: dict = Body(...), user: CurrentUser = Depends(require_user)):
    return call(library.update_collection, user.id, collection_id, body)


@router.get('/artifacts')
def artifacts(user: CurrentUser = Depends(require_user)):
    return {'artifacts': library.artifacts(user.id)}


@router.get('/compatibility')
def compatibility(admin: CurrentUser = Depends(require_admin)):
    from web_app.backend import server
    from auto_2d_drawing.tolerance.native_case_contract import historical_compatibility
    originals = server.case_base.cases
    adapted = [historical_compatibility(case) for case in originals]
    return {'historical_count': len(originals), 'eligible_before': sum(case.is_retrieval_eligible() for case in originals),
            'eligible_after': sum(case.is_retrieval_eligible() for case in adapted),
            'mapping_reasons': dict(Counter(case.source_metadata['native_compatibility']['reason'] or 'COMPATIBLE' for case in adapted)),
            'original_file_modified': False}


@router.get('/cases')
def cases(collection_id: list[str] = Query(default=[]), limit: int = Query(100, ge=1, le=500),
          offset: int = Query(0, ge=0), user: CurrentUser = Depends(require_user)):
    rows = call(library.cases, user.id, collection_id, limit=limit + 1, offset=offset)
    return {'cases': rows[:limit], 'count': min(len(rows), limit), 'has_more': len(rows) > limit}


@router.get('/export')
def export_cases(collection_id: list[str] = Query(default=[]), user: CurrentUser = Depends(require_user)):
    rows = call(library.cases, user.id, collection_id)
    # Exactly the same root array and ToleranceCase fields as the historical DB.
    return Response(json.dumps([row['case'] for row in rows], ensure_ascii=False, indent=2), media_type='application/json',
                    headers={'Content-Disposition': 'attachment; filename="native_tolerance_cases.json"'})


@router.post('/artifacts/{artifact_id}/cases')
def curate_artifact(artifact_id: str, body: dict = Body(...), user: CurrentUser = Depends(require_user)):
    from web_app.backend import server
    if body.get('confirmed') is not True:
        raise HTTPException(400, detail='請先確認選取尺寸與公差可作為未來推薦依據')
    artifact = call(library.artifact, user.id, artifact_id)
    source = artifact['input_snapshot'].get('source_metadata') or {}
    if source.get('candidate_engine') != 'experimental_v026' or not source.get('step_sha256'):
        raise HTTPException(409, detail='此成果沒有新版來源版本證據，請用新版重新產圖後加入')
    assert_model_access(user, artifact['model_id'])
    requested = set(str(value) for value in body.get('rule_ids') or [])
    saved = {str(record.get('rule_id') or record.get('id')): record for record in artifact['input_snapshot'].get('feature_records') or [] if record.get('enabled')}
    if not requested or not requested.issubset(saved):
        raise HTTPException(400, detail='只能加入這張成果實際選取並產生的尺寸')
    collection_ids = list(dict.fromkeys(str(value) for value in body.get('collection_ids') or []))
    owned = {row['id'] for row in library.collections(user.id)}
    if not collection_ids or not set(collection_ids).issubset(owned):
        raise HTTPException(404, detail='案例集不存在或不屬於目前帳號')
    output = server._safe_output_dir(artifact['model_id'])
    part = server._safe_part_id(artifact['part_id'])
    step = next((Path(output) / folder / (part + ext) for folder in ('_parts', '') for ext in ('.stp', '.step') if (Path(output) / folder / (part + ext)).is_file()), None)
    if step is None:
        raise HTTPException(404, detail='找不到來源 STEP，無法驗證原生特徵')
    server.shadow_analysis_slots.acquire(lambda: server.EXPERIMENTAL_SHADOW_MAX_WORKERS, blocking=True)
    try:
        result = server._run_cad_annotation_worker({
            'action': 'native_case_payloads', 'step_path': str(step), 'source_sha256': source['step_sha256'],
            'records': [saved[key] for key in sorted(requested)], 'owner_id': user.id, 'artifact_id': artifact_id,
            'model_id': artifact['model_id'], 'part_id': part, 'operation_id': source.get('operation_id'),
        }, str(Path(output) / '_worker_jobs' / ('curate_' + __import__('uuid').uuid4().hex)))
    except Exception as error:
        raise HTTPException(422, detail='新版案例驗證失敗：' + str(error)) from error
    finally:
        server.shadow_analysis_slots.release()
    return call(library.add_cases, user.id, artifact_id, collection_ids, result['cases'])


@router.delete('/cases/{record_id}')
def remove_case(record_id: str, user: CurrentUser = Depends(require_user)):
    call(library.remove_case, user.id, record_id)
    return {'status': 'removed_from_recommendation'}


@router.post('/cases/{record_id}/share')
def request_share(record_id: str, user: CurrentUser = Depends(require_user)):
    return call(library.request_share, user.id, record_id)


@router.get('/share-requests')
def share_requests(admin: CurrentUser = Depends(require_admin)):
    return {'requests': library.share_requests()}


@router.post('/share-requests/{request_id}/review')
def review_share(request_id: str, body: dict = Body(...), admin: CurrentUser = Depends(require_admin)):
    if not isinstance(body.get('approve'), bool):
        raise HTTPException(400, detail='請明確選擇核准或拒絕')
    return call(library.review_share, admin, request_id, body['approve'], body.get('tag_ids') or [])


def source_path(user, case_id, extension):
    if extension not in {'pdf', 'png', 'svg', 'dxf'}: raise KeyError('不支援的來源格式')
    case = library.case_for_source(user, case_id)
    metadata = case['source_metadata']
    artifact = library.artifact(metadata['owner_user_id'], metadata['source_artifact_id'])
    url = str(artifact['output_files'].get(extension + '_url') or
              (artifact['output_files'].get('pdf_url') if extension == 'svg' else '') or '')
    if not url.startswith('/api/files/'): raise KeyError('來源圖面不存在')
    root = Path(OUTPUT_DIR).resolve()
    path = (root / unquote(url[len('/api/files/'):])).resolve()
    if extension == 'svg': path = path.with_suffix('.svg')
    try: path.relative_to(root)
    except ValueError as error: raise KeyError('來源路徑不合法') from error
    if not path.is_file(): raise KeyError('來源圖面已移除')
    return path


@router.get('/sources/{case_id}/{extension}')
def source_file(case_id: str, extension: str, user: CurrentUser = Depends(require_user)):
    path = call(source_path, user, case_id, extension)
    media = {'pdf': 'application/pdf', 'png': 'image/png', 'svg': 'image/svg+xml', 'dxf': 'application/dxf'}[extension]
    return FileResponse(path, media_type=media, headers={'Content-Disposition': 'inline'})
