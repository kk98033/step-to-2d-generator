export type WorkspaceLocation = {
  modelId: string;
  partId: string | null;
  viewTab: 'main' | 'features3d' | 'smart_annotation';
  engine: 'legacy_smart' | 'experimental_v026';
  drawing?: any;
};

export function readWorkspace(storage: Storage, userId: string): WorkspaceLocation | null {
  try {
    const value = JSON.parse(storage.getItem(`cad-workspace:${userId}`) || 'null');
    if (!value || value.version !== 1 || typeof value.modelId !== 'string' || !value.modelId) return null;
    if (!['main', 'features3d', 'smart_annotation'].includes(value.viewTab)) return null;
    if (!['legacy_smart', 'experimental_v026'].includes(value.engine)) return null;
    return { modelId: value.modelId, partId: typeof value.partId === 'string' ? value.partId : null,
      viewTab: value.viewTab, engine: value.engine, drawing: value.drawing || null };
  } catch { return null; }
}

export function saveWorkspace(storage: Storage, userId: string, value: WorkspaceLocation | null) {
  try {
    if (value) storage.setItem(`cad-workspace:${userId}`, JSON.stringify({ ...value, version: 1 }));
    else storage.removeItem(`cad-workspace:${userId}`);
  } catch { /* Disabled/full storage must not prevent CAD work. */ }
}
