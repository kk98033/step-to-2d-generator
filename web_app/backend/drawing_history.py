"""Read an engineer's saved drawings from durable operation audit records."""
import json
from pathlib import Path


def list_drawing_history(output_dir, user_id, part_id, engine, limit=30):
    root = Path(output_dir) / '_users' / user_id / '_annotation_audit'
    items = []
    examined = 0
    if not root.is_dir():
        return items
    for day in sorted(root.iterdir(), reverse=True):
        if not day.is_dir():
            continue
        for path in sorted(day.glob('*.json'), reverse=True):
            examined += 1
            if examined > 2000:
                return items
            try:
                record = json.loads(path.read_text(encoding='utf-8'))
                request = record.get('request') or {}
                result = record.get('result') or {}
                if (record.get('status') != 'completed'
                        or (record.get('engineer') or {}).get('id') != user_id
                        or request.get('part_id') != part_id
                        or request.get('candidate_engine', 'legacy_smart') != engine
                        or not result.get('png_url')):
                    continue
                operation_id = record.get('operation_id')
                items.append({
                    'operation_id': operation_id,
                    'completed_at': record.get('completed_at') or record.get('created_at'),
                    'candidate_engine': engine,
                    'result': {**result, 'operation_id': operation_id},
                })
                if len(items) >= limit:
                    return items
            except (OSError, ValueError, AttributeError, TypeError):
                continue
    return items
