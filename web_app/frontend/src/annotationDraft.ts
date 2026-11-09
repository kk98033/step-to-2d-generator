export type AnnotationDraft = { selectedIds: string[]; config: Record<string, any> };

export const annotationDraftKey = (user: string, model: string, part: string, engine: string) =>
  'cad-annotation-draft:' + JSON.stringify([user, model, part, engine]);

export function readAnnotationDraft(storage: Storage, key: string): AnnotationDraft | null {
  try {
    const value = JSON.parse(storage.getItem(key) || 'null');
    if (value?.version !== 1 || !Array.isArray(value.selectedIds) || !value.config || Array.isArray(value.config)) return null;
    return { selectedIds: value.selectedIds.filter((id: unknown) => typeof id === 'string'), config: value.config };
  } catch { return null; }
}

export function saveAnnotationDraft(storage: Storage, key: string, draft: AnnotationDraft) {
  try { storage.setItem(key, JSON.stringify({ ...draft, version: 1 })); } catch { /* Optional browser persistence. */ }
}

export function draftFromRecords(records: any[]): AnnotationDraft {
  const config: Record<string, any> = {};
  const selectedIds: string[] = [];
  for (const record of records) {
    const id = record.rule_id || record.id;
    if (typeof id !== 'string') continue;
    const { rule_id: _ruleId, id: _id, ...settings } = record;
    config[id] = settings;
    if (record.enabled) selectedIds.push(id);
  }
  return { selectedIds, config };
}
