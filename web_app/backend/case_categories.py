"""Read-only classification of historical and native cases using existing tag IDs."""


def category_tag_ids(case, tags):
    payload = case.to_dict() if hasattr(case, 'to_dict') else case
    metadata = payload.get('source_metadata') or {}
    part = str(payload.get('part_type') or '').upper()
    feature = str(payload.get('feature_type') or '').lower()
    role = str(payload.get('inferred_role') or metadata.get('measurement_role') or '').lower()
    keys = set()
    if part in {'GENERAL', 'MECHANICAL_PART'}: keys.add('type:general')
    if part == 'SHAFT' or feature in {'shaft_segment', 'locating_shoulder', 'retaining_ring_groove'} or 'shaft' in role:
        keys.add('type:shaft')
    if feature == 'hole': keys.add('type:hole')
    if part in {'FAN', 'IMPELLER', 'BLADE'}: keys.add('type:fan')
    if part in {'HOUSING', 'CASING', 'ENCLOSURE'}: keys.add('type:housing')
    return {tag['id'] for tag in tags if tag.get('dimension') == 'PART_TYPE' and tag.get('key') in keys}


def selected_category_ids(tag_ids, tags):
    source_tags = {tag['id'] for tag in tags if tag.get('dimension') == 'COMPANY_DATABASE'}
    return set(tag_ids or []) - source_tags


def matches_categories(case, selected, mode, tags, assigned=()):
    explicit = set(assigned)
    type_tags = {tag['id'] for tag in tags if tag.get('dimension') == 'PART_TYPE'}
    # A deliberately assigned type takes precedence over a legacy fallback.
    available = explicit | (set() if explicit & type_tags else category_tag_ids(case, tags))
    return not selected or (selected.issubset(available) if mode == 'ALL' else bool(selected & available))


def company_category_case_ids(identity, cases, tag_ids, mode='ANY'):
    tags = identity.list_recommendation_tags()
    selected = selected_category_ids(tag_ids, tags)
    if not selected: return None
    assignments = identity.company_case_tags()
    return {case.case_id for case in cases if matches_categories(case, selected, mode, tags, assignments.get(case.case_id, []))}
